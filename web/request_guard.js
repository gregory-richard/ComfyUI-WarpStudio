// The header every Warp Studio JSON route asks for (see comfy_updater/request_guard.py).
// A browser sends a custom header to another site only after a CORS preflight,
// which ComfyUI refuses, so a request that carries it came from the ComfyUI page.
export const GUARD_HEADERS = Object.freeze({ "X-Civitai-Updater": "1" });
