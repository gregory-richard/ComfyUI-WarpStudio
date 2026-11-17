# --- First-start data migration (WarpPipe / Civitai Updater -> Warp Studio) ---
# Copies old data to the new names once. Must run before warp_pipe is imported
# and before initialize_plugin(). It must never stop ComfyUI from starting.
def _run_data_migration() -> None:
    import logging
    from pathlib import Path

    try:
        import folder_paths
    except ImportError:
        return  # not running inside ComfyUI (test runtime)
    try:
        if __package__:
            from .studio import migration
        else:
            from studio import migration
        migration.migrate(
            Path(folder_paths.get_user_directory()),
            [Path(p) for p in folder_paths.get_folder_paths("custom_nodes")],
            Path(__file__).resolve().parent,
        )
    except Exception as exc:
        logging.getLogger("WarpStudio").warning("Data migration failed: %s", exc)


_run_data_migration()
# --- end data migration ---

if __package__:
    from . import warp_pipe as _warp_pipe
    from .comfy_updater.node_info import NODE_CLASS_MAPPINGS as _UPDATER_NODES
    from .comfy_updater.node_info import NODE_DISPLAY_NAME_MAPPINGS as _UPDATER_DISPLAY_NAMES
    from .comfy_updater.node_info import comfy_entrypoint as _updater_entrypoint
    from .comfy_updater.plugin import initialize_plugin
else:
    # Support direct import smoke tests from the node-pack root.
    import warp_pipe as _warp_pipe
    from comfy_updater.node_info import NODE_CLASS_MAPPINGS as _UPDATER_NODES
    from comfy_updater.node_info import NODE_DISPLAY_NAME_MAPPINGS as _UPDATER_DISPLAY_NAMES
    from comfy_updater.node_info import comfy_entrypoint as _updater_entrypoint
    from comfy_updater.plugin import initialize_plugin

initialize_plugin()

WEB_DIRECTORY = _warp_pipe.WEB_DIRECTORY

if hasattr(_warp_pipe, "comfy_entrypoint"):
    # V3: ComfyUI ignores comfy_entrypoint when NODE_CLASS_MAPPINGS exists, so
    # this branch must not define it. One extension lists both packages' nodes.
    from comfy_api.latest import ComfyExtension

    class _WarpStudioExtension(ComfyExtension):
        async def get_node_list(self):
            warp_pipe_extension = await _warp_pipe.comfy_entrypoint()
            updater_extension = await _updater_entrypoint()
            return [
                *await warp_pipe_extension.get_node_list(),
                *await updater_extension.get_node_list(),
            ]

    async def comfy_entrypoint() -> _WarpStudioExtension:
        return _WarpStudioExtension()

    __all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]
else:
    NODE_CLASS_MAPPINGS = {**_warp_pipe.NODE_CLASS_MAPPINGS, **_UPDATER_NODES}
    NODE_DISPLAY_NAME_MAPPINGS = {
        **_warp_pipe.NODE_DISPLAY_NAME_MAPPINGS,
        **_UPDATER_DISPLAY_NAMES,
    }
    __all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
