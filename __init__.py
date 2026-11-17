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
        logging.getLogger("WarpStudio").warning("Data migration failed: %s", exc, exc_info=True)


# --- end data migration ---


def _detect_old_packages() -> list:
    """Old WarpPipe / Civitai Updater packages that are still active. Never raises."""
    import logging
    from pathlib import Path

    try:
        import folder_paths
    except ImportError:
        return []  # not running inside ComfyUI (test runtime)
    try:
        if __package__:
            from .studio import legacy
        else:
            from studio import legacy
        return legacy.find_active_old_packages(
            [Path(p) for p in folder_paths.get_folder_paths("custom_nodes")],
            Path(__file__).resolve().parent,
        )
    except Exception:
        logging.getLogger("WarpStudio").warning(
            "Could not check for old WarpPipe / Civitai Updater packages", exc_info=True
        )
        return []


def _stand_down(found: list) -> None:
    """Switch off: no nodes, no routes but one, and a notice for the user."""
    import logging

    if __package__:
        from .studio import legacy
    else:
        from studio import legacy
    message = legacy.stand_down_message(found)
    logging.getLogger("WarpStudio").warning(message)
    try:
        from aiohttp import web
        from server import PromptServer

        instance = getattr(PromptServer, "instance", None)
        if instance is not None:

            @instance.routes.get("/warp-studio/standdown")
            async def _standdown_route(_request):
                return web.json_response({"message": message})

    except Exception:
        logging.getLogger("WarpStudio").warning(
            "Could not register the stand-down notice", exc_info=True
        )


_OLD_PACKAGES = _detect_old_packages()

if _OLD_PACKAGES:
    _stand_down(_OLD_PACKAGES)
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
    WEB_DIRECTORY = "./standdown"
    __all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
else:
    _run_data_migration()
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
