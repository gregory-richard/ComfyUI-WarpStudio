import assert from "node:assert/strict";
import { test } from "node:test";

import { forget, loras } from "../../web/library.js";
import { resolveUrl, useApi } from "../../web/api_base.js";
import {
  connectedBase,
  forgetBases,
  describeBase,
  fitsBase,
  fittingFamilies,
  folderBase,
  normaliseBase,
  sameBase,
} from "../../web/model_base.js";

// Index entries as the server builds them: folder is the first directory of the
// file's path inside the loras folder, base_model what its sidecar says.
const lora = (folder, base_model = null) => ({ folder, base_model });

// The real case: a MOSS-Image finetune from Hugging Face, never on Civitai,
// so it has no sidecar and will never get one.
const KROMOSS = "moss\\kromoss - v1.0 int8 (moss).safetensors";

test("a checkpoint with no sidecar is known by its folder, and says so", () => {
  assert.deepEqual(folderBase(KROMOSS), { name: "moss", fromFolder: true });
  assert.deepEqual(folderBase("moss/sub/file.safetensors"), { name: "moss", fromFolder: true });
  assert.equal(folderBase("loose file.safetensors"), null);
  assert.equal(folderBase(""), null);
});

test("on a model known by its folder, the LoRAs filed beside it fit", () => {
  const base = folderBase(KROMOSS);

  // Properly tagged on Civitai: these all read orange before.
  assert.equal(fitsBase(lora("moss", "MOSS Image"), base), true);
  // No sidecar either, but filed in the same place.
  assert.equal(fitsBase(lora("moss"), base), true);
  // Folders are matched the way base names are: case and spelling aside.
  assert.equal(fitsBase(lora("Moss", "MOSS Image"), base), true);
});

test("anywhere else the folder name is still only compared, never assumed", () => {
  const base = folderBase(KROMOSS);

  assert.equal(fitsBase(lora("sdxl", "SDXL 1.0"), base), false);
  // A LoRA that says nothing, filed elsewhere: can't tell.
  assert.equal(fitsBase(lora("misc"), base), null);
  assert.equal(fitsBase(lora("", null), base), null);
  // A sidecar that happens to spell the folder still matches, as it did.
  assert.equal(fitsBase(lora("misc", "moss"), base), true);
});

test("a base read from a sidecar is the rule, and the folder plays no part", () => {
  const base = { name: "Krea 2", fromFolder: false };

  assert.equal(fitsBase(lora("krea2", "Krea 2"), base), true);
  assert.equal(fitsBase(lora("Krea 2", "Flux.2 D"), base), false);
  assert.equal(fitsBase(lora("krea2"), base), null);
});

test("unknown never matches", () => {
  assert.equal(sameBase("", ""), false);
  assert.equal(sameBase(null, undefined), false);
  assert.equal(fitsBase(lora("moss", "MOSS Image"), null), null);
  assert.equal(fitsBase(null, folderBase(KROMOSS)), null);
});

test("the browser's families follow the same rule, largest first", () => {
  const entries = [
    lora("moss", "MOSS Image"),
    lora("moss", "MOSS Image"),
    lora("moss"),
    lora("sdxl", "SDXL 1.0"),
    lora("misc"),
  ];

  const families = fittingFamilies(entries, folderBase(KROMOSS));
  assert.deepEqual(
    [...families],
    [
      ["MOSS Image", 2],
      ["moss", 1],
    ]
  );
  assert.equal(fittingFamilies(entries, null).size, 0);
});

test("the warning names a folder as a folder", () => {
  assert.equal(describeBase(folderBase(KROMOSS)), "a model filed under moss");
  assert.equal(describeBase({ name: "Krea 2", fromFolder: false }), "Krea 2");
});

// The same rules as warp_pipe.normalise_base_model, on its own examples.
test("normaliseBase folds only trivial variants, like the server", () => {
  assert.equal(normaliseBase("Flux.2 Klein 9B-base"), "Flux.2 Klein 9B");
  assert.equal(normaliseBase("  Krea 2 "), "Krea 2");
  assert.notEqual(normaliseBase("Flux.2 Klein 4B"), normaliseBase("Flux.2 Klein 9B"));
  assert.equal(normaliseBase("Flux.1   D"), "Flux.1 D");
  assert.equal(normaliseBase(null), null);
  assert.equal(normaliseBase(""), null);
  assert.equal(normaliseBase("   "), null);
  assert.equal(normaliseBase(42), null);
});

test("normaliseBase folds a Turbo into the base it was distilled from", () => {
  assert.equal(normaliseBase("Krea 2 Turbo"), "Krea 2");
  assert.equal(normaliseBase("Krea 2"), "Krea 2");
  assert.equal(normaliseBase("ZImageTurbo"), "ZImage");
  assert.equal(normaliseBase("ZImageBase"), "ZImage");
  assert.equal(normaliseBase("Krea 2 turbo"), "Krea 2");
  // Only as the last word: anything else is a different name, not a variant.
  assert.equal(normaliseBase("Turbo Diffusion XL"), "Turbo Diffusion XL");
  assert.equal(normaliseBase("Turbo"), null);
});

test("a failed base lookup falls back to the folder without being remembered", async () => {
  const realFetch = globalThis.fetch;
  const node = { id: 1, widgets: [{ name: "ckpt_name", value: "moss/kromoss.safetensors" }] };
  const reply = (ok, body) => async () => ({ ok, status: ok ? 200 : 503, json: async () => body });
  try {
    forgetBases();
    globalThis.fetch = reply(false, { error: "restarting" });
    assert.deepEqual(await connectedBase(node), { name: "moss", fromFolder: true });

    // The server is back: the folder guess was not kept, so it is asked again.
    globalThis.fetch = reply(true, { base_model: "MOSS Image" });
    assert.deepEqual(await connectedBase(node), { name: "MOSS Image", fromFolder: false });

    // A good answer is kept.
    globalThis.fetch = reply(false, {});
    assert.deepEqual(await connectedBase(node), { name: "MOSS Image", fromFolder: false });
  } finally {
    globalThis.fetch = realFetch;
    forgetBases();
  }
});

// ComfyUI under a URL prefix: a fake api that adds one, as the real one does.
const prefixed = (calls) => ({
  fetchApi: async (path, init) => {
    calls.push(["fetchApi", path, init]);
    return {
      ok: true,
      json: async () => ({
        base_model: "SDXL",
        loras: [{ id: "a", thumbnail: "/warp-studio/lora/thumbnail?name=a" }, { id: "b" }],
      }),
    };
  },
  fileURL: (path) => `/comfy${path}`,
});

test("resolveUrl prefixes only root-absolute paths", () => {
  const fileURL = (path) => `/comfy${path}`;
  assert.equal(
    resolveUrl("/warp-studio/lora/thumbnail?name=a", fileURL),
    "/comfy/warp-studio/lora/thumbnail?name=a"
  );
  assert.equal(
    resolveUrl("https://image.civitai.com/x.jpg", fileURL),
    "https://image.civitai.com/x.jpg"
  );
  assert.equal(resolveUrl("//cdn.example/x.png", fileURL), "//cdn.example/x.png");
  assert.equal(resolveUrl("data:image/png;base64,AA", fileURL), "data:image/png;base64,AA");
  assert.equal(resolveUrl("blob:http://h/1", fileURL), "blob:http://h/1");
  assert.equal(resolveUrl("", fileURL), "");
  assert.equal(resolveUrl(undefined, fileURL), "");
  // With no prefix fileURL is the identity, so nothing changes.
  assert.equal(
    resolveUrl("/warp-studio/x?y=1", (path) => path),
    "/warp-studio/x?y=1"
  );
});

test("the base lookup goes through the api, so a URL prefix applies", async () => {
  const calls = [];
  const node = { id: 1, widgets: [{ name: "ckpt_name", value: "sdxl/a.safetensors" }] };
  try {
    forgetBases();
    useApi(prefixed(calls));
    assert.deepEqual(await connectedBase(node), { name: "SDXL", fromFolder: false });
    assert.equal(calls[0][1], "/warp-studio/model/base?name=sdxl%2Fa.safetensors");
    assert.equal(calls[0][2].headers["X-Civitai-Updater"], "1");
  } finally {
    useApi(null);
    forgetBases();
  }
});

test("library thumbnails are given the URL prefix, other entries are untouched", async () => {
  const calls = [];
  try {
    forget();
    useApi(prefixed(calls));
    const list = await loras();
    assert.equal(list[0].thumbnail, "/comfy/warp-studio/lora/thumbnail?name=a");
    assert.equal("thumbnail" in list[1], false);
    assert.equal(calls[0][1], "/warp-studio/loras");
  } finally {
    useApi(null);
    forget();
  }
});
