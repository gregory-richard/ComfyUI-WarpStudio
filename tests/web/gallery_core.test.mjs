import assert from "node:assert/strict";
import { test } from "node:test";

import { cardModel, filterEntries, galleryFacets, groupEntries } from "../../web/gallery_core.js";

const a = {
  id: "pony/maker - glow - v1 (pony).safetensors",
  folder: "pony",
  creator: "maker",
  name: "glow",
  version: "v1",
  base_model: "Pony",
  structured: true,
  title: "Glow",
  triggers: ["glow"],
  has_preview: true,
  thumbnail: "/t?a",
};
const b = {
  id: "sdxl/other - Beam - v2 (sdxl).safetensors",
  folder: "sdxl",
  creator: "other",
  name: "beam",
  version: "v2",
  base_model: "sdxl",
  structured: true,
  title: "beam",
  triggers: [],
  has_preview: false,
  thumbnail: "/t?b",
};
const c = {
  id: "pony/zed.safetensors",
  folder: "pony",
  name: "zed",
  base_model: "Pony",
  structured: false,
};
const d = {
  id: "misc/amber.safetensors",
  folder: "misc",
  name: "Amber",
  base_model: null,
  structured: false,
};
const all = [a, b, c, d];

test("filterEntries matches every term", () => {
  assert.deepEqual(filterEntries(all, { query: "maker glow" }), [a]);
  assert.deepEqual(filterEntries(all, { query: "maker nope" }), []);
});

test("filterEntries ignores missing fields", () => {
  assert.deepEqual(filterEntries(all, { query: "undefined" }), []);
  assert.deepEqual(filterEntries(all, { query: "und" }), []);
});

test("filterEntries with an empty selection matches nothing", () => {
  assert.deepEqual(filterEntries(all, { bases: [] }), []);
  assert.deepEqual(filterEntries(all, { types: [] }), []);
});

test("filterEntries with null selections keeps all", () => {
  assert.deepEqual(filterEntries(all, {}), all);
  assert.deepEqual(filterEntries(all, { types: ["lora"], bases: ["Pony"] }), [a, c]);
});

test("galleryFacets lists types and sorted bases", () => {
  assert.deepEqual(galleryFacets(all), {
    modelTypes: ["lora"],
    baseModels: ["misc", "Pony", "sdxl"],
  });
  assert.deepEqual(galleryFacets([]), { modelTypes: [], baseModels: [] });
});

test("groupEntries puts fitting bands first, then by size", () => {
  const groups = groupEntries(all, new Set(["sdxl"]));
  assert.deepEqual(
    groups.map((g) => [g.base, g.fits]),
    [
      ["sdxl", true],
      ["Pony", false],
      ["misc", false],
    ]
  );
  assert.deepEqual(
    groups[1].entries.map((e) => e.id),
    [a.id, c.id]
  );
});

test("cardModel fills gaps", () => {
  assert.deepEqual(cardModel(d), {
    key: d.id,
    title: "Amber",
    creator: "",
    detail: "misc",
    triggers: 0,
    url: null,
    thumbnail: null,
    stem: "amber",
  });
  assert.equal(cardModel(a).detail, "v1 · Pony");
  assert.equal(cardModel(a).thumbnail, "/t?a");
  assert.equal(cardModel(a).triggers, 1);
  assert.equal(cardModel({ id: "x/y.safetensors", name: "<img src=x>" }).title, "<img src=x>");
});
