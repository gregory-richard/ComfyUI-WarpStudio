import assert from "node:assert/strict";
import { test } from "node:test";

import { guardHeaders } from "../../web/request_guard.js";

// ComfyUI's api.fetchApi writes its Comfy-User header into the object it is given.
function addComfyUser(headers) {
  headers["Comfy-User"] = "someone";
  return headers;
}

test("guardHeaders: carries the panel header", () => {
  assert.deepEqual(guardHeaders(), { "X-Civitai-Updater": "1" });
});

test("guardHeaders: ComfyUI can add its own header", () => {
  assert.doesNotThrow(() => addComfyUser(guardHeaders()));
});

test("guardHeaders: one request's headers never reach the next", () => {
  addComfyUser(guardHeaders());

  assert.equal("Comfy-User" in guardHeaders(), false);
});

test("guardHeaders: extra headers are added beside it", () => {
  assert.deepEqual(guardHeaders({ "Content-Type": "application/json" }), {
    "X-Civitai-Updater": "1",
    "Content-Type": "application/json",
  });
});
