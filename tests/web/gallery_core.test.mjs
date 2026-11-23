import assert from "node:assert/strict";
import { test } from "node:test";

import {
  arrange,
  cardModel,
  filterEntries,
  formatCount,
  galleryFacets,
  foldedIn,
  toggleFolded,
  galleryOnlyBases,
  stackEntries,
} from "../../web/gallery_core.js";

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

const stacksOf = (entries) => entries.map((e) => ({ key: "file:" + e.id, members: [e] }));

test("arrange puts fitting bands first, then by size", () => {
  const groups = arrange(stacksOf(all), {
    groupBy: "base",
    sort: "name",
    fitting: new Set(["sdxl"]),
  });
  assert.deepEqual(
    groups.map((g) => [g.base, g.fits]),
    [
      ["sdxl", true],
      ["Pony", false],
      ["misc", false],
    ]
  );
  assert.deepEqual(
    groups[1].stacks.map((s) => s.members[0].id),
    [a.id, c.id]
  );
});

test("arrange with groupBy none gives one band", () => {
  const groups = arrange(stacksOf(all), { groupBy: "none", sort: "name" });
  assert.equal(groups.length, 1);
  assert.equal(groups[0].base, null);
  assert.deepEqual(
    groups[0].stacks.map((s) => s.members[0].id),
    [d.id, b.id, a.id, c.id]
  );
});

test("arrange sorts newest by the date added", () => {
  const dated = [
    { ...a, added: "2026-01-02T00:00:00Z" },
    { ...b, added: "2026-03-01T00:00:00Z" },
    { ...c, added: null },
    { ...d, added: "2026-02-01T00:00:00Z" },
  ];
  const [band] = arrange(stacksOf(dated), { groupBy: "none", sort: "newest" });
  assert.deepEqual(
    band.stacks.map((s) => s.members[0].id),
    [b.id, d.id, a.id, c.id]
  );
});

const v = (id, version_id, published, extra = {}) => ({
  id,
  folder: "pony",
  name: "glow",
  base_model: "Pony",
  structured: true,
  kind: "loras",
  model_id: 7,
  version_id,
  published,
  ...extra,
});
const v1 = v("pony/maker - glow - v1 (pony).safetensors", 11, "2026-01-01T00:00:00Z");
const v2 = v("pony/maker - glow - v2 (pony).safetensors", 12, "2026-02-01T00:00:00Z");
const v3 = v("pony/maker - glow - v3 (pony).safetensors", 13, "2026-03-01T00:00:00Z");

test("cardModel shows the parsed file name over Civitai's title", () => {
  const model = cardModel({ ...a, title: "Glow Of The Night", kind: "loras" });
  assert.equal(model.title, "glow");
  assert.equal(model.creator, "maker");
  assert.equal(model.detail, "v1 · Pony");
  assert.equal(model.kind, "loras");
  assert.equal(cardModel({ id: "x/y.safetensors", title: "Civ", name: "n" }).title, "Civ");
});

test("filterEntries finds an entry by a word only Civitai's title has", () => {
  const e = { ...a, title: "Glow Of The Night" };
  assert.deepEqual(filterEntries([e, b], { query: "night" }), [e]);
});

test("stackEntries stacks versions of one model and base, newest first", () => {
  const stacks = stackEntries([v1, v3, v2]);
  assert.equal(stacks.length, 1);
  assert.equal(stacks[0].key, "stack:loras:7:Pony");
  assert.deepEqual(stacks[0].members, [v3, v2, v1]);
});

test("stackEntries breaks a date tie by version id", () => {
  const x = v("pony/x.safetensors", 20, "2026-01-01T00:00:00Z");
  const y = v("pony/y.safetensors", 21, "2026-01-01T00:00:00Z");
  assert.deepEqual(stackEntries([x, y])[0].members, [y, x]);
});

test("stackEntries splits one model across two bases", () => {
  const sdxl = v("sdxl/maker - glow - v1 (sdxl).safetensors", 14, null, { base_model: "sdxl" });
  const stacks = stackEntries([v1, sdxl]);
  assert.deepEqual(
    stacks.map((s) => s.key),
    ["stack:loras:7:Pony", "stack:loras:7:sdxl"]
  );
});

test("stackEntries leaves an entry without a model id alone", () => {
  const stacks = stackEntries([v1, c]);
  assert.deepEqual(
    stacks.map((s) => [s.key, s.members.length]),
    [
      ["stack:loras:7:Pony", 1],
      [`file:${c.id}`, 1],
    ]
  );
});

test("test_stack_after_a_member_is_removed", () => {
  const all3 = [v1, v2, v3];
  assert.equal(stackEntries(all3.filter((e) => e !== v3))[0].members[0], v2);
  assert.equal(stackEntries(all3.filter((e) => e !== v3))[0].members.length, 2);
  assert.deepEqual(stackEntries([]), []);
});

test("test_folded_bases_ignore_unknown", () => {
  assert.deepEqual([...foldedIn(["Pony", "Gone"], ["Pony", "sdxl"])], ["Pony"]);
  assert.equal(foldedIn(null, ["Pony"]).size, 0);
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
    missing: false,
    kind: "loras",
  });
  assert.equal(cardModel(a).detail, "v1 · Pony");
  assert.equal(cardModel(a).thumbnail, "/t?a");
  assert.equal(cardModel(a).triggers, 1);
  assert.equal(cardModel({ id: "x/y.safetensors", name: "<img src=x>" }).title, "<img src=x>");
});

test("cardModel marks an entry that is not on this drive", () => {
  assert.equal(cardModel({ ...a, missing: true }).missing, true);
  assert.equal(cardModel(a).missing, false);
});

// The panel's Bases menu also lists the check's raw Civitai spellings, which the
// server folds when it files a LoRA: "ZImageTurbo" is filed under "ZImage".
const z1 = { id: "z/one.safetensors", folder: "z", name: "one", base_model: "ZImage" };
const z2 = { id: "z/two.safetensors", folder: "z", name: "two", base_model: "ZImage" };
const k = { id: "k/kay.safetensors", folder: "k", name: "kay", base_model: "Krea 2" };
const f = { id: "sdxl-turbo/eff.safetensors", folder: "sdxl-turbo", name: "eff", base_model: null };
const folded = [z1, z2, k, f];

test("filterEntries: a selected Turbo or Base spelling shows the LoRAs filed under it", () => {
  assert.deepEqual(filterEntries(folded, { bases: ["ZImageTurbo"] }), [z1, z2]);
  assert.deepEqual(filterEntries(folded, { bases: ["ZImageBase"] }), [z1, z2]);
  assert.deepEqual(filterEntries(folded, { bases: ["Krea 2 Turbo"] }), [k]);
  // Spelling aside, like sameBase.
  assert.deepEqual(filterEntries(folded, { bases: ["zimage"] }), [z1, z2]);
  assert.deepEqual(filterEntries(folded, { bases: ["Krea 2 Turbo", "ZImage"] }), [z1, z2, k]);
});

test("filterEntries: a folder name is still matched as it is", () => {
  // A folder that happens to end in "turbo" is a folder, not a Turbo.
  assert.deepEqual(filterEntries(folded, { bases: ["sdxl-turbo"] }), [f]);
  assert.deepEqual(filterEntries(folded, { bases: ["Turbo"] }), []);
  assert.deepEqual(filterEntries(folded, { bases: ["Flux.1 D"] }), []);
});

test("galleryOnlyBases drops a gallery base another view already names", () => {
  assert.deepEqual(
    galleryOnlyBases(["misc", "Pony", "ZImage", "Krea 2"], ["ZImageTurbo", "Pony", "Krea 2 Turbo"]),
    ["misc"]
  );
  // Neighbouring names are different models and stay.
  assert.deepEqual(galleryOnlyBases(["Flux.2 Klein 9B"], ["Flux.2 Klein 4B"]), ["Flux.2 Klein 9B"]);
  assert.deepEqual(galleryOnlyBases(["ZImage"], []), ["ZImage"]);
  assert.deepEqual(galleryOnlyBases(["ZImage"], [null, "", "Turbo"]), ["ZImage"]);
});

test("foldedIn treats anything but an array as nothing folded", () => {
  assert.equal(foldedIn("Pony", ["Pony"]).size, 0);
  assert.equal(foldedIn({ Pony: true }, ["Pony"]).size, 0);
  assert.equal(foldedIn(7, ["Pony"]).size, 0);
});

test("stackEntries keeps one model id under two kinds as two stacks", () => {
  const model = { ...v1, kind: "checkpoints" };
  const stacks = stackEntries([v1, model]);
  assert.deepEqual(
    stacks.map((s) => s.key),
    ["stack:loras:7:Pony", "stack:checkpoints:7:Pony"]
  );
});

test("stackEntries puts a member with no publish date after dated ones", () => {
  const undated = v("pony/undated.safetensors", 99, null);
  const [stack] = stackEntries([undated, v1, v2]);
  assert.deepEqual(stack.members, [v2, v1, undated]);
  assert.equal(stackEntries([undated, v("pony/also.safetensors", 100, null)])[0].members.length, 2);
});

test("arrange orders a stack by its newest member's title", () => {
  const old = v("pony/old.safetensors", 1, "2026-01-01T00:00:00Z", { name: "zzz" });
  const fresh = v("pony/fresh.safetensors", 2, "2026-02-01T00:00:00Z", { name: "aaa" });
  const mid = { ...b, base_model: "Pony", model_id: null, name: "mmm" };
  const stacks = [...stackEntries([old, fresh]), ...stackEntries([mid])];
  const [band] = arrange(stacks, { groupBy: "none", sort: "name" });
  // The stack shows "aaa", so it sorts before "mmm" although its older member is "zzz".
  assert.deepEqual(
    band.stacks.map((s) => s.members[0].name),
    ["aaa", "mmm"]
  );
});

test("removing every member of a list leaves no stack", () => {
  const list = [v1, v2];
  assert.deepEqual(stackEntries(list.filter((e) => !list.includes(e))), []);
});

test("toggleFolded keeps a folded base that is not listed right now", () => {
  // Folding another band leaves the unlisted "Gone" in storage.
  assert.deepEqual(toggleFolded(["Gone"], "Pony"), ["Gone", "Pony"]);
  // Unfolding one removes only that one.
  assert.deepEqual(toggleFolded(["Gone", "Pony"], "Pony"), ["Gone"]);
  assert.deepEqual(toggleFolded(null, "Pony"), ["Pony"]);
  assert.deepEqual(toggleFolded("Pony", "sdxl"), ["sdxl"]);
});

const withIds = [
  { ...a, model_id: 1 },
  { ...b, model_id: 2 },
  { ...c }, // no model: never has numbers
  { ...d, model_id: 3 },
];
const stats = new Map([
  ["1", { downloads: 100, likes: 9, comments: 2 }],
  ["2", { downloads: 500, likes: 3, comments: 0 }],
  ["3", { downloads: 100, likes: 3, comments: 0 }],
]);
const idsOf = (sort, extra = {}) =>
  arrange(stacksOf(withIds), { groupBy: "none", sort, stats, ...extra })[0].stacks.map(
    (s) => s.members[0].id
  );

test("arrange sorts by downloads, highest first, ties by name, unknown last", () => {
  assert.deepEqual(idsOf("downloads"), [b.id, d.id, a.id, c.id]);
});

test("arrange sorts by likes and by comments the same way", () => {
  assert.deepEqual(idsOf("likes"), [a.id, d.id, b.id, c.id]);
  // A zero is a number: it ranks above a stack with none.
  assert.deepEqual(idsOf("comments"), [a.id, d.id, b.id, c.id]);
});

test("a stat sort with no numbers falls back to name order", () => {
  assert.deepEqual(idsOf("downloads", { stats: new Map() }), [d.id, b.id, a.id, c.id]);
  assert.deepEqual(idsOf("likes", { stats: undefined }), [d.id, b.id, a.id, c.id]);
});

test("a stat sort applies inside each band", () => {
  const groups = arrange(stacksOf(withIds), { groupBy: "base", sort: "downloads", stats });
  const pony = groups.find((g) => g.base === "Pony");
  assert.deepEqual(
    pony.stacks.map((s) => s.members[0].id),
    [a.id, c.id]
  );
});

test("formatCount", () => {
  const cases = [
    [0, "0"],
    [950, "950"],
    [999, "999"],
    [1000, "1k"],
    [11570, "11.6k"],
    [1234567, "1.2M"],
    [999950, "1M"],
    [-1, ""],
    [NaN, ""],
    [Infinity, ""],
  ];
  for (const [input, expected] of cases) assert.equal(formatCount(input), expected, String(input));
});
