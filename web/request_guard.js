// The header every Warp Studio JSON route asks for (see comfy_updater/request_guard.py).
// A browser sends a custom header to another site only after a CORS preflight,
// which ComfyUI refuses, so a request that carries it came from the ComfyUI page.
//
// A fresh object per request: ComfyUI's api.fetchApi adds its own Comfy-User header
// to the object it is given, so a shared or frozen one breaks every later call.
export function guardHeaders(extra = {}) {
  return { "X-Civitai-Updater": "1", ...extra };
}
