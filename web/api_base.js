// How the pack's own modules reach ComfyUI when it is served under a URL
// sub-path (a reverse proxy at https://host/comfy/, some cloud notebooks).
//
// ComfyUI's `api` object knows the prefix; a bare fetch("/warp-studio/...") or a
// root-absolute <img src> does not. `api` is imported lazily and optionally
// here, because /scripts/api.js only exists inside ComfyUI and the modules that
// use this one are also loaded by node tests.

let loaded = null;

/** ComfyUI's api object, or null outside ComfyUI (node tests). */
export function comfyApi() {
  if (!loaded) {
    loaded = import("../../scripts/api.js").then(
      (module) => module.api ?? null,
      () => null
    );
  }
  return loaded;
}

/** Hand in an api object instead of importing one: for tests. null forgets it. */
export function useApi(api) {
  loaded = api === null ? null : Promise.resolve(api);
}

/** A request to one of the pack's routes, with the base path in front.
 *
 * `path` is the route as written in routes.py ("/warp-studio/..."). Without
 * ComfyUI's api (node) it is a plain fetch of the same path.
 */
export async function fetchRoute(path, init) {
  const api = await comfyApi();
  return api ? api.fetchApi(path, init) : fetch(path, init);
}

/** `url` for an <img src>: a root-absolute path gets the base path in front.
 *
 * `fileURL` is api.fileURL, which adds only the base (never "/api"), so with no
 * prefix the URL comes out character for character as it went in. Anything that
 * is not a root-absolute path - https:, data:, blob:, a protocol-relative //host
 * - is already complete and passes through, as does an empty value.
 */
export function resolveUrl(url, fileURL) {
  if (typeof url !== "string" || !url) return "";
  if (!url.startsWith("/") || url.startsWith("//")) return url;
  return typeof fileURL === "function" ? fileURL(url) : url;
}

/** Entries from the library with their thumbnail made loadable under a prefix. */
export async function withThumbnails(entries) {
  const api = await comfyApi();
  // An older frontend has no fileURL; it has no sub-path support to lose either.
  if (!api || typeof api.fileURL !== "function") return entries;
  return entries.map((entry) =>
    entry && entry.thumbnail
      ? { ...entry, thumbnail: resolveUrl(entry.thumbnail, (path) => api.fileURL(path)) }
      : entry
  );
}
