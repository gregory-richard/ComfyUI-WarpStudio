from __future__ import annotations

import copy
import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
import uuid

from .base_models import UNKNOWN_FAMILY, base_family, family_sort_key
from .constants import (
    INFO_SIDECAR_SUFFIX,
    MODEL_PAGE_BASE_URL,
    SUPPORTED_MODEL_EXTENSIONS,
    SUPPORTED_MODEL_TYPES,
)
from .naming_scheme import name_tail, own_name

NAMING_PHASE = "naming"
_MAX_FINISHED_JOBS = 5
# The panel stays bound to the newest job of these types, so pruning never drops it.
_KEEP_NEWEST_TYPES = ("check-updates", "rename-dry-run")
# A dry run is saved across restarts only once completed: the newest completed one stays too.
_KEEP_NEWEST_COMPLETED_TYPES = ("rename-dry-run",)

GROUP_KEYS = ("none", "type", "baseFamily")
MATURE_MODES = ("show", "blur", "hide")
RESULT_MODES = ("updates", "issues")
ISSUE_STATUSES = ("error", "not_found")
# A file Civitai doesn't know but another source identified: shown as Found, not an issue.
FOUND_SOURCES = ("huggingface", "ai-toolkit", "civarchive")
GROUP_PATH_SEPARATOR = "||"
_UNGROUPED_LABEL = "Ungrouped"


@dataclass
class JobRecord:
    id: str
    type: str
    status: str = "queued"
    startedAt: str | None = None
    finishedAt: str | None = None
    progress: int = 0
    total: int = 0
    message: str = ""
    summary: dict = field(default_factory=dict)
    items: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    control: "JobControl | None" = None
    seededPaths: set = field(default_factory=set)
    # finishedAt of a job brought back from disk after a restart, else None.
    restoredFrom: str | None = None
    # What a running check is doing besides its main pass: "naming" while the AI names new releases.
    phase: str = ""

    def as_dict(self, include_items: bool = True) -> dict:
        payload = {
            "jobId": self.id,
            "type": self.type,
            "status": self.status,
            "startedAt": self.startedAt,
            "finishedAt": self.finishedAt,
            "progress": self.progress,
            "total": self.total,
            "message": self.message,
            "phase": self.phase,
            "summary": self.summary,
            "itemCount": len(self.items),
            "errors": self.errors,
        }
        if include_items:
            payload["items"] = self.items
        return payload


class JobManager:
    def __init__(self, archive_store=None, config_store=None, release_names=None) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()
        self.archive_store = archive_store
        self.config_store = config_store
        self.release_names = release_names
        self._finish_listeners: list = []
        self._reserved: str | None = None

    def reserve(self, job_type: str) -> None:
        """Let only ``job_type`` start next: any other job's start is refused until it does.

        This makes the hand-over to a job that waits for the slot certain: a job
        clicked in the gap between one job's end and its listener can't take it.
        """
        with self._lock:
            self._reserved = job_type

    def release_reservation(self) -> None:
        with self._lock:
            self._reserved = None

    def add_finish_listener(self, listener) -> None:
        """Call ``listener(record)`` once after every job ends, on the job thread.

        It runs after the final status is set (completed, failed or cancelled),
        so the slot is free by then. A listener that raises is logged and does
        not stop the others.
        """
        with self._lock:
            self._finish_listeners.append(listener)

    def start(
        self, job_type: str, runner, seed_items: list[dict] | None = None, on_complete=None
    ) -> JobRecord | None:
        """Start a job, or return None when another job is still active.

        ``seed_items`` pre-fills the job with previous results (marked
        ``_seeded``) so the UI keeps showing them while the job re-checks;
        each seeded entry is replaced as a fresh item for its path arrives.
        ``on_complete(snapshot)`` runs on the job thread once the job completed
        (not when it failed or was cancelled), with a deep copy of ``as_dict()``
        taken as it completed; an error in it is printed, the job stays completed.
        """
        job_id = str(uuid.uuid4())
        control = JobControl(job_id)
        record = JobRecord(id=job_id, type=job_type, control=control)
        if seed_items:
            record.items = list(seed_items)
            record.seededPaths = {
                str(item.get("modelPath") or "").lower()
                for item in seed_items
                if item.get("modelPath")
            }
        with self._lock:
            for job in self._jobs.values():
                if job.status in ("running", "queued", "paused"):
                    return None
            if self._reserved is not None:
                if job_type != self._reserved:
                    return None
                self._reserved = None
            self._jobs[job_id] = record
            self._prune_finished_locked()

        thread = threading.Thread(
            target=self._run_job,
            args=(record, runner, control, on_complete),
            daemon=True,
            name=f"civitai-updater-{job_type}-{job_id[:8]}",
        )
        thread.start()
        return record

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def sync_seeds_with_disk(
        self,
        job_id: str,
        model_types: list[str],
        current_paths: set[str],
        added_seeds: list[dict],
    ) -> None:
        """Align a job's seeded items with the files that exist right now.

        Seeds come from the previous check, so they miss files added since and
        still list files removed since. A stale seed makes the provisional view
        offer a release the user already downloaded, so seeds for vanished
        files are dropped and ``added_seeds`` (already marked ``_seeded``) are
        added for new ones. ``current_paths`` must be lower-cased and covers
        only ``model_types``; seeds of other types are left alone.
        """
        checked_types = set(model_types)
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            kept: list[dict] = []
            for item in job.items:
                key = str(item.get("modelPath") or "").lower()
                if (
                    item.get("_seeded")
                    and not item.get("archived")
                    and item.get("modelType") in checked_types
                    and key not in current_paths
                ):
                    job.seededPaths.discard(key)
                    continue
                kept.append(item)
            job.items = kept
            present = {str(item.get("modelPath") or "").lower() for item in job.items}
            for seed in added_seeds:
                key = str(seed.get("modelPath") or "").lower()
                if not key or key in present:
                    continue
                job.items.append(seed)
                job.seededPaths.add(key)
                present.add(key)

    def get_items(
        self,
        job_id: str,
        offset: int = 0,
        limit: int = 25,
        mode: str | None = None,
        model_types: list[str] | None = None,
        base_models: list[str] | None = None,
        sort: str | None = None,
        show_hidden: bool = False,
        group_by: str | None = None,
        then_by: str | None = None,
        mature: str = "show",
        collapsed: list[str] | None = None,
    ) -> dict | None:
        """Return one page of result cards plus the outline of every group.

        Paging stays over cards so the rendered list is bounded no matter how
        large the library is; a group that runs past the page break is flagged
        so the panel can repeat its header as a continuation.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            items = list(job.items)
            provisional = job.status in ("running", "queued", "paused")
            # A row stamped after the check finished came from a download since.
            finished_at = None if provisional else job.finishedAt

        all_groups = _group_items_by_model(
            items,
            archived_versions=self._archived_snapshot(),
            provisional=provisional,
            downloaded_after=_parse_iso(finished_at or ""),
            names=self.release_names.labels() if self.release_names else None,
        )

        grouped = all_groups
        if mode in RESULT_MODES:
            grouped = [g for g in grouped if _in_mode(g, mode, show_hidden)]

        # The filter menus serve every view (Updates, Issues, Rename): their
        # options come from all of the check's models, not just this view's.
        available_types: set[str] = set()
        available_bases: set[str] = set()
        for group in all_groups:
            model_type = group.get("modelType", "")
            if model_type:
                available_types.add(model_type)
            for local_version in group.get("localVersions", []):
                base_model = local_version.get("baseModel", "")
                if base_model:
                    available_bases.add(base_model)

        mature_mode = mature if mature in MATURE_MODES else "show"
        grouped, mature_hidden = _filter_groups(grouped, model_types, base_models, mature_mode)
        # What each view would list under the same filters, so the panel can
        # label its Updates / Issues switch without a second request.
        filtered_all, _ = _filter_groups(all_groups, model_types, base_models, mature_mode)
        # Updates counts models with a new version, not the ones kept for a download.
        mode_counts = {
            name: sum(
                1 for group in filtered_all if _in_mode(group, name, show_hidden, with_downloaded=False)
            )
            for name in RESULT_MODES
        }

        grouped = _sort_grouped(grouped, sort)

        group_by = group_by if group_by in GROUP_KEYS else "none"
        then_by = then_by if then_by in GROUP_KEYS else "none"
        if then_by == group_by:
            then_by = "none"

        outline, ordered = _build_group_outline(grouped, group_by, then_by)

        collapsed_set = set(collapsed or [])
        visible = [
            card for card in ordered
            if card.get("groupPrimaryKey", "") not in collapsed_set
            and card.get("groupPathKey", "") not in collapsed_set
        ]

        total = len(visible)
        safe_offset = max(0, min(offset, total))
        safe_limit = max(1, min(limit, 500))
        page = visible[safe_offset : safe_offset + safe_limit]

        starts_mid_primary = False
        starts_mid_secondary = False
        if page and safe_offset > 0:
            previous = visible[safe_offset - 1]
            starts_mid_primary = previous.get("groupPrimaryKey") == page[0].get("groupPrimaryKey")
            starts_mid_secondary = previous.get("groupPathKey") == page[0].get("groupPathKey")

        return {
            "total": total,
            "offset": safe_offset,
            "limit": safe_limit,
            "items": page,
            "facets": {
                "modelTypes": sorted(available_types),
                "baseModels": sorted(available_bases),
            },
            "groups": outline,
            "grouping": {"groupBy": group_by, "thenBy": then_by},
            "startsMidPrimary": starts_mid_primary,
            "startsMidSecondary": starts_mid_secondary,
            "matureHidden": mature_hidden,
            "matureMode": mature_mode,
            "modeCounts": mode_counts,
            "collapsed": sorted(collapsed_set),
        }

    def summarize_check_items(self, summary: dict, items: list[dict]) -> dict:
        grouped = _group_items_by_model(items, archived_versions=self._archived_snapshot(), provisional=False)
        with_updates = sum(1 for group in grouped if group.get("hasUpdate"))
        hidden_updates = sum(1 for group in grouped if group.get("hasHiddenUpdates"))
        merged = dict(summary or {})
        merged["withUpdates"] = with_updates
        merged["hiddenUpdates"] = hidden_updates
        return merged

    def archived_update_count(self, items: list[dict]) -> int:
        """How many models held only in the archive have a new version the Updates list shows.

        ``items`` are the drive's and the archive's together: a version on the
        drive is not new, and the Hide list applies, as in the list itself.
        """
        grouped = _group_items_by_model(items, archived_versions=self._archived_snapshot(), provisional=False)
        return sum(1 for group in grouped if group.get("archived") and group.get("hasUpdate"))

    def job_ids(self, job_type: str) -> list[str]:
        """Ids of every job of this type still held, finished or not."""
        with self._lock:
            return [job.id for job in self._jobs.values() if job.type == job_type]

    def edit_items(self, job_type: str, edit) -> None:
        """Call ``edit(items)`` on every held job of this type, under the lock (items change in place)."""
        with self._lock:
            for job in self._jobs.values():
                if job.type == job_type:
                    edit(job.items)

    def get_active(self) -> JobRecord | None:
        with self._lock:
            for job in self._jobs.values():
                if job.status in ("running", "queued", "paused"):
                    return job
        return None

    def load_cached_check(self, cache_data: dict, extra_items=()) -> JobRecord:
        """Bring back the last check as the "cached" job; ``extra_items`` (the saved archive rows) join its items."""
        items = [_normalize_cached_item_urls(item) for item in cache_data.get("items", []) or []]
        items += [_normalize_cached_item_urls(item) for item in extra_items if isinstance(item, dict)]
        summary = cache_data.get("summary", {})
        checked_at = cache_data.get("checkedAt", "")

        record = JobRecord(
            id="cached",
            type="check-updates",
            status="completed",
            startedAt=checked_at,
            finishedAt=checked_at,
            summary=summary,
            items=items,
            progress=len(items),
            total=len(items),
            message="Cached",
        )
        with self._lock:
            self._jobs["cached"] = record
        return record

    def restore_job(
        self,
        job_type: str,
        job_id: str,
        *,
        startedAt: str,
        finishedAt: str,
        summary: dict,
        items: list[dict],
    ) -> JobRecord:
        """Register a job finished before a restart under its old id, so stored ids still resolve."""
        record = JobRecord(
            id=job_id,
            type=job_type,
            status="completed",
            startedAt=startedAt,
            finishedAt=finishedAt,
            summary=summary,
            items=items,
            progress=len(items),
            total=len(items),
            message="Restored",
            restoredFrom=finishedAt,
        )
        with self._lock:
            self._jobs[job_id] = record
        return record

    def pause(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "running" or not job.control or job.finishedAt:
                # A finished job still saving its results has nothing left to pause.
                return job
            job.control.pause()
            job.status = "paused"
            job.message = "Paused"
            return job

    def resume(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != "paused" or not job.control:
                return job
            job.control.resume()
            job.status = "running"
            job.message = "Resumed"
            return job

    def set_phase(self, job_id: str, phase: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.phase = phase

    def naming_cards(self, items: list[dict]) -> list[dict]:
        """The check's cards as the panel groups them: hidden releases are not in ``newVersions``."""
        return _group_items_by_model(items, archived_versions=self._archived_snapshot())

    def cancel(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status in ("completed", "failed", "cancelled"):
                return job
            if job.phase == NAMING_PHASE and job.control:
                # Stop ends only the naming: the check's results are done and stay.
                job.control.stop_phase()
                if job.status == "paused":
                    job.status = "running"
                job.message = "Stopping the naming"
                return job
            if job.control:
                job.control.cancel()
            job.message = "Stopping"
            if job.status == "queued":
                job.status = "cancelled"
                job.finishedAt = _utc_now()
                job.message = "Cancelled"
            return job

    def _run_job(self, job: JobRecord, runner, control: "JobControl", on_complete=None) -> None:
        try:
            self._execute_job(job, runner, control, on_complete)
        finally:
            self._notify_finished(job)

    def _notify_finished(self, job: JobRecord) -> None:
        with self._lock:
            listeners = list(self._finish_listeners)
        for listener in listeners:
            try:
                listener(job)
            except Exception as exc:  # noqa: BLE001
                print(f"Civitai updater: a finish listener failed after the {job.type} job ({exc}).")

    def _execute_job(self, job: JobRecord, runner, control: "JobControl", on_complete=None) -> None:
        with self._lock:
            job.status = "running"
            job.startedAt = _utc_now()

        def progress(current: int, total: int, message: str) -> None:
            with self._lock:
                job.progress = max(0, int(current))
                job.total = max(0, int(total))
                job.message = message or ""

        def emit_item(item: dict) -> None:
            with self._lock:
                key = str(item.get("modelPath") or "").lower()
                if key and key in job.seededPaths:
                    job.seededPaths.discard(key)
                    job.items = [
                        existing
                        for existing in job.items
                        if not (
                            existing.get("_seeded")
                            and str(existing.get("modelPath") or "").lower() == key
                        )
                    ]
                job.items.append(item)

        try:
            summary, items = runner(progress, emit_item, control)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if control.is_cancelled():
                    job.status = "cancelled"
                    job.finishedAt = _utc_now()
                    job.message = "Cancelled"
                    return
                job.status = "failed"
                job.finishedAt = _utc_now()
                job.errors.append(str(exc))
            return

        snapshot = None
        with self._lock:
            if control.is_cancelled():
                job.status = "cancelled"
                job.message = "Cancelled"
                # Keep the accumulated items — seeds that were not re-checked
                # yet plus the fresh partial results — so cancelling does not
                # blank the previous check's results.
            else:
                job.items = items or job.items
                job.seededPaths = set()
            job.finishedAt = _utc_now()
            job.summary = summary or {}
            job.progress = job.total
            if job.status != "cancelled":
                if on_complete is not None:
                    # The job stays active until the hook returns, so no edit or apply
                    # can land while it saves (and be lost, or reset by the save).
                    snapshot = copy.deepcopy(job.as_dict())
                    snapshot["status"] = "completed"
                else:
                    self._publish_completed_locked(job)
            elif not job.message:
                job.message = "Completed"

        if snapshot is not None:
            try:
                on_complete(snapshot)
            except Exception as exc:  # noqa: BLE001
                print(f"Civitai updater: could not save the finished {job.type} job ({exc}).")
            with self._lock:
                self._publish_completed_locked(job)

    @staticmethod
    def _publish_completed_locked(job: JobRecord) -> None:
        job.status = "completed"
        if not job.message or job.message.startswith("Stopping"):
            # A stop that came after the runner returned changes nothing: it completed.
            job.message = "Completed"

    def _prune_finished_locked(self) -> None:
        """Drop all but the most recent finished jobs so item lists don't accumulate forever."""
        finished = [
            job
            for job in self._jobs.values()
            if job.status in ("completed", "failed", "cancelled") and job.id != "cached"
        ]
        if len(finished) <= _MAX_FINISHED_JOBS:
            return
        finished.sort(key=lambda job: job.finishedAt or "", reverse=True)
        # The panel stays bound to the last check's results (and the last dry
        # run) however many scans ran since, so those jobs are never dropped.
        keep = set()
        for job_type in _KEEP_NEWEST_TYPES:
            newest = next((job for job in finished if job.type == job_type), None)
            if newest is not None:
                keep.add(newest.id)
        for job_type in _KEEP_NEWEST_COMPLETED_TYPES:
            newest = next((job for job in finished if job.type == job_type and job.status == "completed"), None)
            if newest is not None:
                keep.add(newest.id)
        for job in finished[_MAX_FINISHED_JOBS:]:
            if job.id not in keep:
                self._jobs.pop(job.id, None)

    def _archived_snapshot(self) -> dict[str, set[str]]:
        if not self.archive_store:
            return {}
        return self.archive_store.snapshot()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _type_order(model_type: str) -> int:
    """Checkpoints first, then LoRA, VAE, UNet, embeddings - the order people
    think about their library in, not alphabetical order."""
    try:
        return SUPPORTED_MODEL_TYPES.index((model_type or "").lower())
    except ValueError:
        return len(SUPPORTED_MODEL_TYPES)


def _group_value(card: dict, key: str) -> tuple[str, str, tuple]:
    """Return (group key, display label, sort key) for one card."""
    if key == "type":
        model_type = (card.get("modelType") or "").lower()
        label = model_type.capitalize() if model_type else _UNGROUPED_LABEL
        return (label, label, (_type_order(model_type), label.lower()))
    if key == "baseFamily":
        family = card.get("baseFamily") or UNKNOWN_FAMILY
        return (family, family, family_sort_key(family))
    return ("", "", (0, ""))


def _build_group_outline(
    cards: list[dict], group_by: str, then_by: str
) -> tuple[list[dict], list[dict]]:
    """Order cards by group and produce the outline with full counts.

    Counts come from every card in the result set, not just the current page,
    so a header still reads correctly when its group spans several pages.
    """
    if group_by == "none":
        for card in cards:
            card["groupPrimary"] = ""
            card["groupSecondary"] = ""
            card["groupPrimaryKey"] = ""
            card["groupPathKey"] = ""
        return ([], list(cards))

    primaries: dict[str, dict] = {}
    for card in cards:
        p_key, p_label, p_sort = _group_value(card, group_by)
        s_key, s_label, s_sort = _group_value(card, then_by) if then_by != "none" else ("", "", (0, ""))
        path_key = f"{p_key}{GROUP_PATH_SEPARATOR}{s_key}" if s_key else p_key

        card["groupPrimary"] = p_label
        card["groupSecondary"] = s_label
        card["groupPrimaryKey"] = p_key
        card["groupPathKey"] = path_key

        primary = primaries.setdefault(p_key, {
            "key": p_key, "label": p_label, "sort": p_sort,
            "models": 0, "releases": 0, "subgroups": {}, "cards": [],
        })
        releases = len(card.get("newVersions") or [])
        primary["models"] += 1
        primary["releases"] += releases

        if s_key:
            secondary = primary["subgroups"].setdefault(s_key, {
                "key": path_key, "label": s_label, "sort": s_sort,
                "models": 0, "releases": 0, "cards": [],
            })
            secondary["models"] += 1
            secondary["releases"] += releases
            secondary["cards"].append(card)
        else:
            primary["cards"].append(card)

    ordered_primaries = sorted(primaries.values(), key=lambda g: g["sort"])
    outline: list[dict] = []
    ordered_cards: list[dict] = []

    for primary in ordered_primaries:
        subgroups = sorted(primary["subgroups"].values(), key=lambda g: g["sort"])
        outline.append({
            "key": primary["key"],
            "label": primary["label"],
            "models": primary["models"],
            "releases": primary["releases"],
            "subgroups": [
                {"key": sub["key"], "label": sub["label"],
                 "models": sub["models"], "releases": sub["releases"]}
                for sub in subgroups
            ],
        })
        if subgroups:
            for sub in subgroups:
                ordered_cards.extend(sub["cards"])
        else:
            ordered_cards.extend(primary["cards"])

    return (outline, ordered_cards)


def _days_behind(newest_local: str, newest_remote: str) -> int:
    """Whole days between the newest version held locally and the newest
    release. Zero when nothing newer exists or a date is unusable."""
    local = _parse_iso(newest_local)
    remote = _parse_iso(newest_remote)
    if not local or not remote or remote <= local:
        return 0
    return (remote - local).days


def _parse_iso(value: str):
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Another tool's sidecar may carry a bare date or no offset; read it as
    # UTC, since comparing it with Civitai's aware dates would raise.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize_cached_item_urls(item: dict) -> dict:
    if not isinstance(item, dict):
        return item

    normalized = dict(item)
    for key in ("modelUrl", "versionUrl"):
        normalized[key] = _normalize_civitai_model_page_url(normalized.get(key, ""))

    remote_versions = []
    for version in normalized.get("remoteVersions", []) or []:
        if not isinstance(version, dict):
            remote_versions.append(version)
            continue
        version_copy = dict(version)
        version_copy["versionUrl"] = _normalize_civitai_model_page_url(version_copy.get("versionUrl", ""))
        remote_versions.append(version_copy)
    normalized["remoteVersions"] = remote_versions
    return normalized


def _normalize_civitai_model_page_url(url: str) -> str:
    """Rewrite model page links saved before the civitai.red switch."""
    if not isinstance(url, str) or not url:
        return ""

    legacy_bases = (
        "https://civitai.com/models",
        "http://civitai.com/models",
        "https://civitai.red/models",
        "http://civitai.red/models",
    )
    for legacy_base in legacy_bases:
        if url.startswith(legacy_base):
            return f"{MODEL_PAGE_BASE_URL}{url[len(legacy_base):]}"
    return url


def _group_items_by_model(
    items: list[dict],
    archived_versions: dict[str, set[str]] | None = None,
    provisional: bool = False,
    downloaded_after: datetime | None = None,
    names: dict | None = None,
) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    ungrouped: list[dict] = []

    for item in items:
        key = str(item.get("modelId") or "")
        if item.get("archived") and not key:
            # An archived file Civitai could not place would only be noise.
            continue
        if not key:
            key = item.get("modelUrl") or ""
        if not key:
            ungrouped.append(item)
            continue
        groups.setdefault(key, []).append(item)

    result: list[dict] = []
    archived_versions = archived_versions or {}

    for model_id, members in groups.items():
        result.append(
            _build_group(
                model_id, members, archived_versions.get(model_id, set()), provisional, downloaded_after, names
            )
        )

    for item in ungrouped:
        result.append(_build_ungrouped_item(item, provisional))

    return result


def _added_after(member: dict, after: datetime | None) -> bool:
    """A drive row stamped after ``after``: a download made since the check finished.

    A check stamps its rows while it runs; ``check_records.downloaded_item`` stamps
    the download's time. Archived rows never count: the archive pass stamps them
    after the drive's pass has ended.
    """
    if after is None or member.get("archived"):
        return False
    stamp = _parse_iso(member.get("lastCheckedAt", ""))
    return stamp is not None and stamp > after


def _build_group(
    model_id: str,
    members: list[dict],
    archived_ids: set[str],
    provisional: bool,
    downloaded_after: datetime | None = None,
    names: dict | None = None,
) -> dict:
    names = names or {}
    representative = members[0]
    creator_name = ""
    model_url = ""
    model_name = representative.get("modelName", "") or _filename(representative.get("modelPath", ""))
    model_type = representative.get("modelType", "")
    local_versions: list[dict] = []
    local_keys: set[tuple[str, str]] = set()
    local_version_ids: set[str] = set()
    remote_versions_by_id: dict[str, dict] = {}

    for member in members:
        creator_name = creator_name or member.get("creatorName", "")
        model_url = model_url or member.get("modelUrl", "")
        model_name = model_name or member.get("modelName", "")
        model_type = model_type or member.get("modelType", "")

        local_version_id = str(member.get("localVersionId") or "")
        local_key = (member.get("modelPath", ""), local_version_id)
        if local_key not in local_keys:
            local_keys.add(local_key)
            local_versions.append(
                {
                    "versionId": local_version_id,
                    "versionName": member.get("localVersionName", ""),
                    "baseModel": member.get("baseModel", ""),
                    "publishedAt": member.get("localVersionDate", ""),
                    "modelPath": member.get("modelPath", ""),
                    "filePath": _file_path(member),
                    "metadataOnly": bool(member.get("metadataOnly")),
                    "archived": bool(member.get("archived")),
                    "previewUrl": member.get("localPreviewUrl", ""),
                    "previewType": member.get("localPreviewType", "image"),
                    "fallbackSource": member.get("fallbackSource", ""),
                    "hasLocalPreview": bool(member.get("hasLocalPreview")),
                    "status": member.get("status", ""),
                    "error": member.get("error", ""),
                    "creatorName": member.get("creatorName", ""),
                    "creatorStatus": member.get("creatorStatus", ""),
                    "downloaded": (
                        not member.get("deleted")
                        and not member.get("movedAt")
                        and _added_after(member, downloaded_after)
                    ),
                    "deleted": bool(member.get("deleted")),
                    "moved": bool(member.get("moved")),
                    **_row_names(member),
                }
            )
        # A deleted file is not held any more: its release can be new again.
        if local_version_id and not member.get("deleted"):
            local_version_ids.add(local_version_id)

        for remote_version in member.get("remoteVersions", []):
            version_id = str(remote_version.get("versionId") or "")
            if not version_id:
                continue
            current = remote_versions_by_id.get(version_id)
            if not current or (remote_version.get("versionDate", "") > current.get("versionDate", "")):
                remote_versions_by_id[version_id] = {
                    "versionId": version_id,
                    "versionName": remote_version.get("versionName", ""),
                    "versionDate": remote_version.get("versionDate", ""),
                    "baseModel": remote_version.get("baseModel", ""),
                    "previewUrl": remote_version.get("previewUrl", ""),
                    "previewType": remote_version.get("previewType", "image"),
                    "versionUrl": remote_version.get("versionUrl", ""),
                    "downloadUrl": remote_version.get("downloadUrl", ""),
                    "availability": remote_version.get("availability", ""),
                    **_paid_access_now(remote_version),
                }

    # The same release held on the drive and in the archive is a duplicate:
    # the drive copy is flagged with the archived file's name. When the drive
    # holds only its info file, that leftover is flagged as such.
    archived_names: dict[str, str] = {}
    for local_version in local_versions:
        version_id = local_version.get("versionId", "")
        if (
            local_version.get("archived")
            and not local_version.get("deleted")
            and version_id
            and version_id not in archived_names
        ):
            archived_names[version_id] = _filename(
                local_version.get("filePath") or local_version.get("modelPath") or ""
            )
    for local_version in local_versions:
        version_id = local_version.get("versionId", "")
        # A row moved to the archive is that archived file, not a second copy of it.
        if local_version.get("moved"):
            continue
        if not local_version.get("archived") and version_id in archived_names:
            local_version["alsoArchived"] = archived_names[version_id]
            if local_version.get("metadataOnly"):
                local_version["infoOnly"] = True
    _mark_deleted_issues(local_versions)
    # A moved row is gone from its card, but an Issues card it made stays listed.
    deleted_issues = sum(1 for version in local_versions if version.get("issueWas"))
    local_versions = [version for version in local_versions if not version.get("moved")]
    archived_only = bool(local_versions) and all(version.get("archived") for version in local_versions)

    # A local version without a date cannot be compared, and an empty newest
    # date would make every other release look new. Civitai reports the date
    # of that same version in the remote list, so take it from there.
    for local_version in local_versions:
        if local_version.get("publishedAt"):
            continue
        remote_match = remote_versions_by_id.get(local_version.get("versionId", ""))
        if remote_match and remote_match.get("versionDate"):
            local_version["publishedAt"] = remote_match["versionDate"]

    local_versions.sort(
        key=lambda version: (version.get("publishedAt", ""), (version.get("versionName") or "").lower()),
        reverse=True,
    )
    # A deleted file is not held: the newest release you have decides what is new.
    held = [version for version in local_versions if not version.get("deleted")] or local_versions
    newest_local_date = max((version.get("publishedAt", "") for version in held if version.get("publishedAt")), default="")

    candidates = []
    # With no usable local date there is nothing to compare against, so no
    # release is claimed as new rather than all of them.
    if newest_local_date:
        for remote_version in remote_versions_by_id.values():
            version_id = remote_version.get("versionId", "")
            version_date = remote_version.get("versionDate", "")
            if not version_date or version_id in local_version_ids:
                continue
            if version_date <= newest_local_date:
                continue
            candidates.append(remote_version)

    candidates.sort(key=lambda version: (version.get("versionDate", ""), version.get("versionName", "").lower()), reverse=True)

    new_versions = [version for version in candidates if version.get("versionId", "") not in archived_ids]
    hidden_versions = [version for version in candidates if version.get("versionId", "") in archived_ids]
    for version in new_versions + hidden_versions:
        label = names.get(version.get("versionId", "")) or {}
        version["fileStem"] = label.get("stem", "")
        version["rowName"] = label.get("label", "")
    primary_visible = new_versions[0] if new_versions else {}
    primary_any = primary_visible or (hidden_versions[0] if hidden_versions else {})
    local_preview = next((version for version in local_versions if version.get("previewUrl")), {})
    nsfw = any(bool(member.get("nsfw")) for member in members)
    # Archived files never count as issues; a drive copy of an archived
    # release does, once.
    issue_count = sum(
        1
        for version in local_versions
        if not version.get("archived")
        and _is_issue(version)
    )

    # A model page can carry releases on several base models, so the card is
    # filed under the base of the release it is headlining, and every row still
    # prints its own exact base.
    group_base = (
        primary_visible.get("baseModel")
        or primary_any.get("baseModel")
        or (local_versions[0].get("baseModel", "") if local_versions else "")
    )

    # Pick preview url and type as a pair so a local video preview is never
    # labeled with the remote entry's (empty-preview) "image" type.
    if primary_any.get("previewUrl"):
        preview_url = primary_any.get("previewUrl", "")
        preview_type = primary_any.get("previewType") or "image"
    else:
        preview_url = local_preview.get("previewUrl", "")
        preview_type = local_preview.get("previewType") or "image"

    return {
        "modelId": model_id,
        "modelType": model_type,
        "modelName": model_name,
        "creatorName": creator_name,
        **_name_fields(local_versions, model_name, creator_name),
        "modelStats": next(
            (member["modelStats"] for member in members if isinstance(member.get("modelStats"), dict)), {}
        ),
        "modelUrl": model_url,
        # Only a card still built from the previous check's results is
        # provisional; one whose files were re-checked already is final.
        "isProvisional": provisional and any(member.get("_seeded") for member in members),
        "issueCount": issue_count,
        "deletedIssues": deleted_issues,
        "archived": archived_only,
        "hasUpdate": bool(new_versions),
        "hasHiddenUpdates": bool(hidden_versions),
        "hasAnyUpdate": bool(new_versions or hidden_versions),
        # Kept in Updates until the next check, so the old versions can still be deleted.
        "downloaded": any(version["downloaded"] for version in local_versions),
        "newestLocalVersionDate": newest_local_date,
        "localVersions": local_versions,
        "newVersions": new_versions,
        "hiddenNewVersions": hidden_versions,
        "versionOrder": card_version_order(local_versions, new_versions, hidden_versions),
        "latestVersionId": primary_visible.get("versionId", ""),
        "latestVersionName": primary_visible.get("versionName", ""),
        "latestBaseModel": primary_visible.get("baseModel", ""),
        "latestVersionDate": primary_visible.get("versionDate", ""),
        "latestAvailability": primary_visible.get("availability", ""),
        "sortVersionDate": primary_any.get("versionDate", ""),
        "groupBaseModel": group_base,
        "baseFamily": base_family(group_base),
        "daysBehind": _days_behind(newest_local_date, primary_visible.get("versionDate", "")),
        "previewUrl": preview_url,
        "previewType": preview_type,
        "versionUrl": primary_any.get("versionUrl", ""),
        "downloadUrl": primary_any.get("downloadUrl", ""),
        "nsfw": nsfw,
    }


def card_version_order(local_versions: list[dict], new_versions: list[dict], hidden_versions: list[dict]) -> list[list]:
    """The card's rows as one list, newest release on top: ``[kind, index]``
    pairs into the saved, new and hidden lists. Files without a date go last.
    On the same date a release goes above a saved file."""
    rows = [("new", index, v.get("versionDate") or "") for index, v in enumerate(new_versions)]
    rows += [("hidden", index, v.get("versionDate") or "") for index, v in enumerate(hidden_versions)]
    rows += [("saved", index, v.get("publishedAt") or "") for index, v in enumerate(local_versions)]
    # A stable sort keeps each list's own order among equal dates.
    rows.sort(key=lambda row: (bool(row[2]), row[2]), reverse=True)
    return [[kind, index] for kind, index, _ in rows]


def _build_ungrouped_item(item: dict, provisional: bool) -> dict:
    local_versions = [
        {
            "versionId": str(item.get("localVersionId") or ""),
            "versionName": item.get("localVersionName", ""),
            "baseModel": item.get("baseModel", ""),
            "publishedAt": item.get("localVersionDate", ""),
            "modelPath": item.get("modelPath", ""),
            "filePath": _file_path(item),
            "metadataOnly": bool(item.get("metadataOnly")),
            "archived": False,
            "previewUrl": item.get("localPreviewUrl", ""),
            "previewType": item.get("localPreviewType", "image"),
            "fallbackSource": item.get("fallbackSource", ""),
            "hasLocalPreview": bool(item.get("hasLocalPreview")),
            "status": item.get("status", ""),
            "error": item.get("error", ""),
            "creatorName": item.get("creatorName", ""),
            "creatorStatus": item.get("creatorStatus", ""),
            "deleted": bool(item.get("deleted")),
            "moved": bool(item.get("moved")),
            **_row_names(item),
        }
    ]
    _mark_deleted_issues(local_versions)
    deleted_issues = sum(1 for version in local_versions if version.get("issueWas"))
    local_versions = [version for version in local_versions if not version.get("moved")]
    model_name = item.get("modelName", "") or _filename(item.get("modelPath", ""))
    return {
        "modelId": "",
        "modelType": item.get("modelType", ""),
        "modelName": model_name,
        "creatorName": item.get("creatorName", ""),
        **_name_fields(local_versions, model_name, item.get("creatorName", "")),
        "modelStats": item["modelStats"] if isinstance(item.get("modelStats"), dict) else {},
        "modelUrl": item.get("modelUrl", ""),
        "isProvisional": provisional and bool(item.get("_seeded")),
        "issueCount": sum(1 for version in local_versions if _is_issue(version)),
        "deletedIssues": deleted_issues,
        "archived": False,
        "hasUpdate": False,
        "hasHiddenUpdates": False,
        "hasAnyUpdate": False,
        "newestLocalVersionDate": item.get("localVersionDate", ""),
        "localVersions": local_versions,
        "newVersions": [],
        "hiddenNewVersions": [],
        "versionOrder": card_version_order(local_versions, [], []),
        "latestVersionId": "",
        "latestVersionName": "",
        "latestBaseModel": "",
        "latestVersionDate": "",
        "latestAvailability": item.get("latestAvailability", ""),
        "sortVersionDate": "",
        "groupBaseModel": item.get("baseModel", ""),
        "baseFamily": base_family(item.get("baseModel", "")),
        "daysBehind": 0,
        "previewUrl": item.get("previewUrl", ""),
        "previewType": item.get("previewType", "image"),
        "versionUrl": item.get("versionUrl", ""),
        "downloadUrl": item.get("downloadUrl", ""),
        "nsfw": bool(item.get("nsfw")),
    }


_VALID_SORTS = ("name", "name-desc", "type", "latest-date", "latest-date-desc", "behind")


def _sort_grouped(items: list[dict], sort: str | None) -> list[dict]:
    if not sort or sort not in _VALID_SORTS:
        sort = "name"
    if sort == "name":
        return sorted(items, key=_title_key)
    if sort == "name-desc":
        return sorted(items, key=_title_key, reverse=True)
    if sort == "type":
        return sorted(items, key=lambda group: ((group.get("modelType") or ""), _title_key(group)))
    if sort == "latest-date":
        return sorted(items, key=lambda group: group.get("sortVersionDate") or "")
    if sort == "latest-date-desc":
        return sorted(items, key=lambda group: group.get("sortVersionDate") or "", reverse=True)
    if sort == "behind":
        # Furthest behind first: the models that drifted the most.
        return sorted(
            items,
            key=lambda group: (-int(group.get("daysBehind") or 0), _title_key(group)),
        )
    return items


def _title_key(group: dict) -> str:
    return (group.get("title") or group.get("modelName") or "").lower()


def move_key(path: str) -> str:
    """A stable key for a file's move, so the panel never sends or shows its path."""
    digest = hashlib.sha256(os.path.normcase(os.path.normpath(path)).encode("utf-8")).hexdigest()
    return "move:" + digest[:16]


def _stem_of(path: str) -> str:
    """A file's name without a supported model extension or the info sidecar's."""
    name = _filename(path)
    lowered = name.lower()
    for suffix in (INFO_SIDECAR_SUFFIX, *SUPPORTED_MODEL_EXTENSIONS):
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _row_names(member: dict) -> dict:
    """The file name, the row's name and the move key of one local row."""
    weights = _file_path(member)
    return {
        "fileName": _filename(weights),
        "rowName": name_tail(_stem_of(weights)),
        "moveKey": move_key(member.get("modelPath", "")),
    }


def _name_fields(local_versions: list[dict], model_name: str, creator_name: str) -> dict:
    """The card's title and creator: your own file's names when they parse, else Civitai's."""
    stems = [
        _stem_of(version.get("filePath") or version.get("modelPath") or "")
        for version in local_versions
        if not version.get("deleted")
    ]
    own = own_name(stems)
    return {
        "title": own.title if own else model_name,
        "creator": own.creator if own else creator_name,
        "ownName": {"creator": own.creator, "title": own.title, "version": own.version} if own else None,
    }


def _is_issue(version: dict) -> bool:
    """Whether a drive row belongs in Issues. Archived rows never do; a found file only when also archived.

    A deleted row is no issue any more; ``issueWas`` keeps its card listed until the next check.
    """
    if version.get("deleted"):
        return False
    return _issue_kind(version)


def _mark_deleted_issues(local_versions: list[dict]) -> None:
    """Flag each deleted row that was an issue, so Issues keeps listing it (not counting it)."""
    for version in local_versions:
        if version.get("deleted") and _issue_kind(version):
            version["issueWas"] = True


def _issue_kind(version: dict) -> bool:
    if version.get("archived"):
        return False
    if version.get("alsoArchived"):
        return True
    if version.get("status") == "not_found" and version.get("fallbackSource") in FOUND_SOURCES:
        return False
    return version.get("status") in ISSUE_STATUSES


def _in_mode(group: dict, mode: str, show_hidden: bool, *, with_downloaded: bool = True) -> bool:
    if mode == "updates":
        return bool(
            group.get("hasUpdate")
            or (show_hidden and group.get("hasHiddenUpdates"))
            or (with_downloaded and group.get("downloaded"))
        )
    if mode == "issues":
        return bool(group.get("issueCount") or (with_downloaded and group.get("deletedIssues")))
    return True


def _filter_groups(
    groups: list[dict],
    model_types: list[str] | None,
    base_models: list[str] | None,
    mature_mode: str,
) -> tuple[list[dict], int]:
    """Apply the type, base and mature filters; return the kept groups and how
    many mature ones were hidden."""
    if model_types is not None:
        type_set = set(model_types)
        groups = [g for g in groups if g.get("modelType", "") in type_set]
    if base_models is not None:
        base_set = set(base_models)
        groups = [
            g for g in groups
            if any(lv.get("baseModel", "") in base_set for lv in g.get("localVersions", []))
        ]
    mature_hidden = 0
    if mature_mode == "hide":
        before = len(groups)
        groups = [g for g in groups if not g.get("nsfw")]
        mature_hidden = before - len(groups)
    return groups, mature_hidden


def _paid_access_now(version: dict) -> dict:
    """The Buzz paywall of a release as it stands today.

    Results are cached for hours or days, and an early-access window can close
    in between, so an expired one is dropped here rather than when checked.
    """
    paid = version.get("paid", "")
    paid_until = version.get("paidUntil", "")
    if paid == "early" and paid_until:
        ends = _parse_iso(paid_until)
        if ends and ends <= datetime.now(timezone.utc):
            return {"paid": "", "paidUntil": ""}
    return {"paid": paid, "paidUntil": paid_until if paid == "early" else ""}


def _file_path(item: dict) -> str:
    """The weights file a local version row copies, never its sidecar."""
    if item.get("filePath"):
        return item["filePath"]
    # Results cached before filePath existed carry only the sidecar path of a
    # metadata-only entry; the extension then falls back to .safetensors.
    model_path = item.get("modelPath", "")
    if model_path.lower().endswith(INFO_SIDECAR_SUFFIX):
        return model_path[: -len(INFO_SIDECAR_SUFFIX)] + ".safetensors"
    return model_path


def _filename(path: str) -> str:
    index = max(path.rfind("/"), path.rfind("\\"))
    return path[index + 1 :] if index >= 0 else path


class JobControl:
    def __init__(self, job_id: str = "") -> None:
        # Lets a runner address its own job before start() has returned.
        self.job_id = job_id
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._phase_stop_event = threading.Event()

    def stop_phase(self) -> None:
        """End the current phase (the naming) only; the job itself goes on to complete."""
        self._phase_stop_event.set()
        self._pause_event.clear()

    def phase_stopped(self) -> bool:
        return self._phase_stop_event.is_set()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._pause_event.clear()

    def pause(self) -> None:
        if not self._cancel_event.is_set():
            self._pause_event.set()

    def resume(self) -> None:
        self._pause_event.clear()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def wait_if_paused(self) -> None:
        while self._pause_event.is_set() and not self._cancel_event.is_set():
            time.sleep(0.1)
