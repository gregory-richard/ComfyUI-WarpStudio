"""Archive and Unarchive: one version's files moved between this drive and the archive drive.

The order keeps the files safe. The weights are copied through a ``.part`` file
while they are hashed, flushed to the drive, read back, and checked against the
SHA-256 their info file lists (without one, against the hash of what was read
from the original). Only then is the ``.part`` renamed to the weights' name, and
their sidecars follow, each one created new. Only once every copy is in place
are the originals sent to the Recycle Bin, the weights first: weights the
Recycle Bin refuses (a LoRA ComfyUI holds open) keep their sidecars, and both
copies stay. Nothing is ever overwritten, renamed or deleted for good.

A failure or a Stop before the weights are in place leaves the original as it
was, and removes the ``.part`` file and every folder the move made (when empty).
Once the weights are in place, Stop no longer applies: what is left is quick,
and stopping half-way would leave the version half on each drive.
"""

from __future__ import annotations

import copy
import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .check_records import update_check_records
from .constants import INFO_SIDECAR_SUFFIX
from .download_queue import MB, DownloadRequest
from .hashing import HashingCancelled, sha256_file
from .model_actions import WEIGHTS_STILL_THERE, _reason
from .model_download import DownloadError, DownloadStopped, _move_into_place, file_sha256
from .model_files import stem_files
from .rename_apply import send_to_trash
from .sidecar import preview_path_for, read_json

# The download queue's sources of a move: Archive (to the archive) and Unarchive (back).
MOVE_SOURCES = ("archive", "unarchive")
COPY_CHUNK = 8 * MB

NO_ARCHIVE_FOLDER_ERROR = "Set an archive folder first."
NO_TRASH_MOVE_ERROR = "Archive and Unarchive need the send2trash package."
NOT_ON_DRIVE_ERROR = "That file is not on this drive."
NOT_IN_ARCHIVE_ERROR = "That file is not in the archive."
INFO_ONLY_MOVE_ERROR = "Only the info file is on this drive, so there is nothing to move."
UNIDENTIFIED_MOVE_ERROR = (
    "Only a file Civitai identified can be archived: the archive check lists files by their info."
)
NOT_IN_TYPE_FOLDER_ERROR = "This file is in no model folder, so it has no place in the archive."
NO_DRIVE_FOLDER_ERROR = "The archive's {folder} folder matches no model folder on this drive."
NAME_TAKEN_ERROR = "A file named {name} is already there."
MOVE_BUSY_ERROR = "That file is being moved. Try again when it's done."
HASH_MISMATCH_ERROR = (
    "The copy didn't match its SHA-256, so it was removed. The original is untouched."
)
BOTH_COPIES_WARNING = "Both copies are kept: {name} could not go to the Recycle Bin ({reason})."
SIDECAR_COPY_WARNING = "Both copies are kept: {name} could not be copied ({reason})."
ARCHIVE_CHANGED_ERROR = "The archive folder changed since this move was asked for. Try it again."
# The routes say these in the same words (routes.py keeps its own copies for its other actions).
FILE_GONE_ERROR = "That file is not there any more."
ARCHIVE_UNREACHABLE_ERROR = "The archive folder can't be reached."


# --- where a file goes ---------------------------------------------------------


def parts_under(path: Path, root: Path) -> Optional[tuple]:
    """``path``'s parts below ``root`` (empty for the root itself), or None when it lies outside.

    Both are made absolute first, so a ``..`` can't lead out of the root
    unseen; case does not matter on Windows.
    """
    full = os.path.abspath(str(path))
    base = os.path.abspath(str(root))
    try:
        if os.path.normcase(os.path.commonpath([full, base])) != os.path.normcase(base):
            return None
    except ValueError:  # on another drive
        return None
    relative = os.path.relpath(full, base)
    if relative == os.curdir:
        return ()
    parts = Path(relative).parts
    return None if ".." in parts else tuple(parts)


def archive_target(weights: Path, archive_root: Path, type_roots: list) -> Optional[Path]:
    """Where Archive puts ``weights``: the same subfolders under the archive's type folder.

    The deepest model folder that holds the file is its type folder. The
    archive's folder of that name (case does not matter) is used when it has
    one, else one of the model folder's name, made by the move. None when the
    file lies in no model folder.
    """
    best: Optional[tuple] = None
    for root in type_roots:
        root = Path(root)
        parts = parts_under(weights, root)
        if not parts or not root.name:
            continue
        if best is None or len(parts) < len(best[1]):
            best = (root, parts)
    if best is None:
        return None
    root, parts = best
    return _archive_type_folder(Path(archive_root), root.name).joinpath(*parts)


def _archive_type_folder(archive_root: Path, name: str) -> Path:
    wanted = name.casefold()
    try:
        children = sorted(archive_root.iterdir(), key=lambda child: child.name.casefold())
    except OSError:
        children = []
    for child in children:
        if child.name.casefold() == wanted and child.is_dir():
            return child
    return archive_root / name


def unarchive_target(weights: Path, archive_root: Path, type_roots: list) -> Optional[Path]:
    """Where Unarchive puts ``weights``: the model folder named like its archive type folder.

    Every subfolder is kept, even one this drive lacks (the move makes it).
    None when the file lies outside the archive or in no type folder, or when
    no existing model folder has that name.
    """
    parts = parts_under(weights, archive_root)
    if not parts or len(parts) < 2:
        return None
    wanted = parts[0].casefold()
    for root in type_roots:
        root = Path(root)
        if root.name.casefold() == wanted and _is_dir(root):
            return root.joinpath(*parts[1:])
    return None


def taken_name(files: Iterable[Path], folder: Path) -> str:
    """The first of ``files`` whose name ``folder`` already has, in any case; "" when none.

    A folder that isn't there yet has none; one that can't be read raises ``OSError``.
    """
    try:
        present = {name.casefold() for name in os.listdir(folder)}
    except FileNotFoundError:
        return ""
    for path in files:
        if path.name.casefold() in present:
            return path.name
    return ""


def info_hashes(info_path: Optional[Path]) -> set:
    """Every SHA-256 an info file lists for its version's files, lower-cased."""
    if info_path is None:
        return set()
    info = read_json(Path(info_path))
    files = info.get("files") if isinstance(info, dict) else None
    if not isinstance(files, list):
        return set()
    return {file_sha256(entry) for entry in files if isinstance(entry, dict)} - {""}


# --- the copy ------------------------------------------------------------------


def copy_verified(
    source: Path, target: Path, expected: set, *, report, should_stop
) -> None:
    """Copy the weights to ``target`` through ``<target>.part``, checked before they get their name.

    The copy is hashed as it is written, flushed to the drive and read back.
    It passes when the read-back hash is the written one and, when ``expected``
    has hashes, one of them. Any failure or Stop removes the ``.part`` and
    raises: ``DownloadStopped``, or ``DownloadError``. Nothing is overwritten,
    not even a ``.part`` left behind by an earlier run.
    """
    source, target = Path(source), Path(target)
    name = target.name
    part = target.with_name(name + ".part")
    if os.path.lexists(target):
        raise DownloadError(NAME_TAKEN_ERROR.format(name=name))
    try:
        writer = open(part, "xb")
    except FileExistsError as exc:
        raise DownloadError(NAME_TAKEN_ERROR.format(name=part.name)) from exc
    except OSError as exc:
        raise DownloadError(f"Could not copy {name}: {_reason(exc)}") from exc
    try:
        with writer:
            total = source.stat().st_size
            digest = hashlib.sha256()
            written = 0
            with open(source, "rb") as reader:
                while True:
                    _stop_if_asked(should_stop)
                    chunk = reader.read(COPY_CHUNK)
                    if not chunk:
                        break
                    writer.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    report(written, total, f"Copying {name}: {written // MB} of {total // MB} MB")
            writer.flush()
            os.fsync(writer.fileno())
        report(total, total, f"Checking {name}")
        try:
            copied = sha256_file(part, chunk_size=COPY_CHUNK, should_stop=should_stop)
        except HashingCancelled as exc:
            raise DownloadStopped() from exc
        if written != total or copied != digest.hexdigest() or (expected and copied not in expected):
            raise DownloadError(HASH_MISMATCH_ERROR)
        _stop_if_asked(should_stop)
        _move_into_place(part, target)
    except FileExistsError as exc:  # the name was taken while the copy ran
        _discard(part)
        raise DownloadError(NAME_TAKEN_ERROR.format(name=name)) from exc
    except OSError as exc:
        _discard(part)
        raise DownloadError(f"Could not copy {name}: {_reason(exc)}") from exc
    except DownloadError as exc:
        _discard(part)
        # A rename that kept failing says why without its paths, like every other copy error.
        if isinstance(exc.__cause__, OSError):
            raise DownloadError(f"Could not copy {name}: {_reason(exc.__cause__)}") from exc
        raise
    except BaseException:
        _discard(part)
        raise
    _copy_times(source, target)


# --- one move ------------------------------------------------------------------


@dataclass
class MoveContext:
    job_manager: Any
    data_dir: Path
    archive_folder: str
    trash: Callable[[str], None] = send_to_trash


def move_one(request: DownloadRequest, *, report, should_stop, ctx: MoveContext) -> None:
    """Move one version's files, or raise ``DownloadError`` (the row shows its text) or a Stop.

    ``request.move_from`` is the weights, ``request.move_to`` their new path
    and ``request.check_item`` their row. ``request.file_name`` is set to the
    weights' name, and warnings are appended to ``request.warnings``.
    """
    to_archive = request.source == "archive"
    source = Path(os.path.normpath(request.move_from))
    target = Path(os.path.normpath(request.move_to))
    request.file_name = source.name

    files, base, below = _check_again(source, target, to_archive, ctx)
    _stop_if_asked(should_stop)
    made = _make_folders(base, below)
    info = next((path for path in files[1:] if path.name.lower().endswith(INFO_SIDECAR_SUFFIX)), None)
    try:
        copy_verified(source, target, info_hashes(info), report=report, should_stop=should_stop)
    except BaseException:
        _remove_empty(made)
        raise

    # The weights are in place and checked; nothing below raises, and Stop no longer applies.
    copied = _copy_sidecars(files[1:], target.parent, request)
    if len(copied) == len(files) - 1:
        _bin_originals(source, files[1:], request, ctx)
    info_copy = target.parent / info.name if info is not None and info in copied else None
    _record(
        request,
        ctx,
        gone=not os.path.lexists(source),
        weights=target,
        info_path=info_copy,
        to_archive=to_archive,
    )


def moved_item(item: dict, *, weights: Path, info_path: Optional[Path], to_archive: bool) -> dict:
    """The row of a file in its new place, until the next check; ``item`` is not changed."""
    moved = copy.deepcopy(item)
    moved.pop("deleted", None)
    moved.pop("moved", None)
    moved["movedAt"] = datetime.now(timezone.utc).isoformat()
    if to_archive:
        # The archive check lists a file by its info, as an info-only row.
        moved["archived"] = True
        moved["metadataOnly"] = True
        moved["modelPath"] = str(info_path or weights)
        moved["filePath"] = str(weights)
        moved["hasLocalPreview"] = False
    else:
        moved.pop("archived", None)
        moved["metadataOnly"] = False
        moved["modelPath"] = moved["filePath"] = str(weights)
        moved["hasLocalPreview"] = preview_path_for(Path(weights)).exists()
    return moved


# --- the steps -----------------------------------------------------------------


def _check_again(source: Path, target: Path, to_archive: bool, ctx: MoveContext) -> tuple:
    """Refuse a move that no longer fits: the request may have waited a while.

    Returns the files to move (weights first), the existing folder the move
    may make folders below, and the parts of the target's folder below it.
    """
    if not ctx.archive_folder:
        raise DownloadError(NO_ARCHIVE_FOLDER_ERROR)
    archive_root = Path(ctx.archive_folder)
    if not _is_dir(archive_root):
        raise DownloadError(ARCHIVE_UNREACHABLE_ERROR)
    in_archive = parts_under(target if to_archive else source, archive_root)
    if not in_archive or len(in_archive) < 2:
        raise DownloadError(ARCHIVE_CHANGED_ERROR)
    files = stem_files(source)
    if not files:
        raise DownloadError(FILE_GONE_ERROR)
    if to_archive:
        base = archive_root
    else:
        # The model folder on this drive: never made by a move, only the folders below it.
        depth = len(in_archive) - 2
        base = target.parents[depth] if depth < len(target.parents) else None
        if base is None or base.name.casefold() != in_archive[0].casefold() or not _is_dir(base):
            raise DownloadError(NO_DRIVE_FOLDER_ERROR.format(folder=in_archive[0]))
    below = parts_under(target.parent, base)
    if below is None:
        raise DownloadError(ARCHIVE_CHANGED_ERROR)
    try:
        taken = taken_name(files, target.parent)
    except OSError as exc:
        raise DownloadError(f"Could not look in the folder {target.parent.name}: {_reason(exc)}") from exc
    if taken:
        raise DownloadError(NAME_TAKEN_ERROR.format(name=taken))
    return files, base, below


def _make_folders(base: Path, below: tuple) -> list:
    """Make the folders ``below`` the existing ``base`` that are missing; returns those made, top first."""
    made: list = []
    current = base
    try:
        for part in below:
            current = current / part
            if current.is_dir():
                continue
            try:
                os.mkdir(current)
            except FileExistsError:
                if current.is_dir():
                    continue  # made meanwhile, not by this move
                raise
            made.append(current)
    except OSError as exc:
        _remove_empty(made)
        raise DownloadError(f"Could not make the folder {current.name}: {_reason(exc)}") from exc
    return made


def _remove_empty(made: list) -> None:
    """Remove the folders a failed move made, deepest first, while they are empty."""
    for folder in reversed(made):
        try:
            folder.rmdir()
        except OSError:
            pass


def _copy_sidecars(sidecars: list, folder: Path, request: DownloadRequest) -> list:
    """Copy each sidecar beside the new weights; returns the originals that were copied."""
    copied = []
    for path in sidecars:
        try:
            _copy_new(path, folder / path.name)
        except Exception as exc:  # noqa: BLE001 - a warning: the originals then stay
            request.warnings.append(SIDECAR_COPY_WARNING.format(name=path.name, reason=_reason(exc)))
            continue
        copied.append(path)
    return copied


def _copy_new(source: Path, target: Path) -> None:
    """Copy a small file to a name that must be free; a half copy is not left behind."""
    with open(source, "rb") as reader:
        writer = open(target, "xb")
        try:
            with writer:
                shutil.copyfileobj(reader, writer, COPY_CHUNK)
        except BaseException:
            _discard(target)  # this move's own half copy, never a file that was there
            raise
    _copy_times(source, target)


def _bin_originals(source: Path, sidecars: list, request: DownloadRequest, ctx: MoveContext) -> None:
    """Send the originals to the Recycle Bin: the weights first, their sidecars only once they are gone."""
    try:
        ctx.trash(os.path.normpath(str(source)))
        refused = WEIGHTS_STILL_THERE if os.path.lexists(source) else ""
    except Exception as exc:  # noqa: BLE001 - both copies stay, and the row says so
        refused = _reason(exc)
    if refused:
        request.warnings.append(BOTH_COPIES_WARNING.format(name=source.name, reason=refused))
        return
    for path in sidecars:
        try:
            ctx.trash(os.path.normpath(str(path)))
        except Exception as exc:  # noqa: BLE001 - the rest still go
            request.warnings.append(
                f"Could not send {path.name} to the Recycle Bin: {_reason(exc)}"
            )


def _record(request, ctx: MoveContext, *, gone: bool, weights: Path, info_path, to_archive: bool) -> None:
    """Give the file its row in its new place; its old row goes only when its weights are gone.

    A failure costs only a warning: the files are where they should be, and
    the next check lists them.
    """
    item = request.check_item if isinstance(request.check_item, dict) else {}
    try:
        added = moved_item(item, weights=weights, info_path=info_path, to_archive=to_archive)
        model_path = str(item.get("modelPath") or "")
        update_check_records(
            ctx.job_manager,
            ctx.data_dir,
            remove=[model_path] if gone and model_path else [],
            add=[added],
            archive_folder=ctx.archive_folder,
            moved=True,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Civitai updater: could not update the check results ({exc})")
        request.warnings.append(f"Could not update the check results: {_reason(exc)}")


# --- small helpers -------------------------------------------------------------


def _copy_times(source: Path, target: Path) -> None:
    """Keep the original's times, so the Gallery's "Newest on disk" doesn't jump."""
    try:
        shutil.copystat(source, target)
    except OSError:
        pass


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _is_dir(folder: Path) -> bool:
    try:
        return folder.is_dir()
    except OSError:
        return False


def _stop_if_asked(should_stop) -> None:
    if should_stop():
        raise DownloadStopped()
