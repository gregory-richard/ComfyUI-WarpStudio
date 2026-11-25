from __future__ import annotations

from pathlib import Path

from .archived_updates import ArchivedUpdateStore
from .config_store import ConfigStore
from .jobs import JobManager
from .rename_store import AnswerCache, BaseCodeStore, DecisionStore, DryRunStore, RenameJournal
from .release_names import ReleaseNameStore
from .routes import register_routes
from .updater_service import UpdaterService

_INITIALIZED = False


def data_dir_for(root_dir: Path) -> Path:
    """Where the updater keeps its data: the ComfyUI user folder, or the package folder."""
    try:
        import folder_paths
    except ImportError:
        return root_dir / ".civitai_updater"
    return Path(folder_paths.get_user_directory()) / "warp-studio" / "updater"


def initialize_plugin() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    root_dir = Path(__file__).resolve().parents[1]
    data_dir = data_dir_for(root_dir)

    config_store = ConfigStore(data_dir)
    archive_store = ArchivedUpdateStore(data_dir)
    updater_service = UpdaterService(config_store)
    base_codes = BaseCodeStore(data_dir)
    release_names = ReleaseNameStore(data_dir, base_codes)
    job_manager = JobManager(archive_store, config_store, release_names=release_names)
    register_routes(
        config_store,
        updater_service,
        job_manager,
        archive_store,
        decisions=DecisionStore(data_dir),
        base_codes=base_codes,
        answer_cache=AnswerCache(data_dir),
        journal=RenameJournal(data_dir),
        dry_runs=DryRunStore(data_dir),
        release_names=release_names,
    )

    _INITIALIZED = True
