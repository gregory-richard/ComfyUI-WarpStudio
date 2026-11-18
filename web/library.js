// The model library, fetched once and shared.
//
// Both views want it: the prompt colours its tags against it and completes from
// it, and the browser lists it. They used to hold a cache each and fetch
// /warp-studio/loras separately, which is the expensive call on the server - it
// reads a sidecar for every file in the folder. One cache means one fetch, and
// it also means the two can never be describing different libraries.

let pending = null;
let cached = null;
let missingPending = null;
let missingCached = null;

// Throws on failure rather than returning nothing: the browser wants to say why
// it is empty, and the prompt - which does not care - catches at its own call.
async function ask(url, key) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);
  return (await response.json())[key] || [];
}

/** Every LoRA. */
export async function loras() {
  return (await everything()).filter((entry) => entry.kind !== "embeddings");
}

/** Every LoRA and embedding, in one list. Each carries its own `kind`. */
export function everything() {
  if (cached) return Promise.resolve(cached);
  if (pending) return pending;

  const mine = Promise.all([
    ask("/warp-studio/loras", "loras"),
    // An installation may have no embeddings folder configured, and that is not
    // a reason for the LoRAs to be unavailable too.
    ask("/warp-studio/embeddings", "embeddings").catch(() => []),
  ])
    .then(([lora, embedding]) => {
      // forget() may have run since: a stale list must not become the cache.
      const list = [...lora, ...embedding];
      if (pending === mine) cached = list;
      return list;
    })
    .finally(() => {
      if (pending === mine) pending = null;
    });
  pending = mine;
  return mine;
}

/** The LoRAs Civitai knows of that are not on this drive, with one shared request. */
export function missingLoras() {
  if (missingCached) return Promise.resolve(missingCached);
  if (missingPending) return missingPending;
  const mine = ask("/warp-studio/loras/missing", "loras")
    .then((list) => {
      if (missingPending === mine) missingCached = list;
      return list;
    })
    .finally(() => {
      if (missingPending === mine) missingPending = null;
    });
  missingPending = mine;
  return mine;
}

/** Forget it, so a model added since is picked up on the next ask. */
export function forget() {
  cached = null;
  pending = null;
  missingCached = null;
  missingPending = null;
}
