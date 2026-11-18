import assert from "node:assert/strict";
import { test } from "node:test";

import { cardModel } from "../../web/gallery_core.js";
import {
  COPY,
  bandHtml,
  cardHtml,
  safeUrl,
  targetHtml,
  typeSwitchHtml,
  versionMenuHtml,
} from "../../web/gallery_view.js";

// The panel's escapeHtml, as it is.
const escapeHtml = (text) =>
  String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const ui = (fields) => ({ mark: null, versions: 1, versionLabel: "v1", ...fields });

const model = (fields) => ({
  key: "pony/maker - glow - v1 (pony).safetensors",
  title: "Glow",
  creator: "maker",
  detail: "v1 · Pony",
  triggers: 0,
  url: null,
  thumbnail: null,
  stem: "maker - glow - v1 (pony)",
  missing: false,
  ...fields,
});

test("cardHtml escapes an HTML title", () => {
  const html = cardHtml(model({ title: "<img src=x onerror=1>" }), ui(), escapeHtml);
  assert.ok(html.includes("&lt;img src=x onerror=1&gt;"));
  assert.ok(!html.includes("<img"));
});

test("cardHtml escapes every other field too", () => {
  const evil = '"><script>x</script>';
  const html = cardHtml(
    model({
      key: evil,
      creator: evil,
      detail: evil,
      stem: evil,
      triggers: 2,
      url: `https://example.test/${evil}`,
      thumbnail: `/t?${evil}`,
    }),
    ui(),
    escapeHtml
  );
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes(evil));
  // key, stem (the tooltip), thumbnail, creator, detail and url: six escaped copies.
  assert.equal(html.split("&quot;&gt;&lt;script&gt;").length - 1, 6);
});

test("cardHtml keeps an empty creator row, so heights match", () => {
  const html = cardHtml(model({ creator: "" }), ui(), escapeHtml);
  assert.ok(html.includes('<span class="cu-g-creator"></span>'));
});

test("cardHtml keeps every row from cardModel's gaps", () => {
  const html = cardHtml(
    cardModel({ id: "misc/amber.safetensors", folder: "misc", name: "Amber" }),
    ui(),
    escapeHtml
  );
  assert.ok(html.includes('<span class="cu-g-creator"></span>'));
  assert.ok(html.includes('<span class="cu-g-title">Amber</span>'));
  assert.ok(html.includes('<span class="cu-g-detail">misc</span>'));
});

test("cardHtml shows a lazy thumbnail, or the stem without one", () => {
  const withImage = cardHtml(model({ thumbnail: "/t?a&b" }), ui(), escapeHtml);
  assert.ok(withImage.includes('<img src="/t?a&amp;b" alt="" loading="lazy"'));
  assert.ok(!withImage.includes("cu-g-noimg"));
  const without = cardHtml(model(), ui(), escapeHtml);
  assert.ok(without.includes('<span class="cu-g-noimg">maker - glow - v1 (pony)</span>'));
});

test("cardHtml has one button for the face, and the link beside it", () => {
  const html = cardHtml(model({ url: "https://example.test/models/1" }), ui(), escapeHtml);
  assert.equal(html.match(/<button/g).length, 1);
  assert.ok(html.indexOf("</button>") < html.indexOf("<a "));
  assert.ok(html.includes('href="https://example.test/models/1"'));
  assert.ok(html.includes('target="_blank" rel="noopener noreferrer"'));
});

test("cardHtml shows the badge only with trigger words", () => {
  assert.ok(!cardHtml(model(), ui(), escapeHtml).includes("cu-g-badge"));
  const html = cardHtml(model({ triggers: 3 }), ui(), escapeHtml);
  assert.ok(html.includes('title="3 trigger words"'));
  assert.ok(html.includes("⊕ 3</span>"));
});

test("cardHtml links only to a web page", () => {
  assert.ok(!cardHtml(model(), ui(), escapeHtml).includes("<a "));
  assert.ok(!cardHtml(model({ url: "javascript:alert(1)" }), ui(), escapeHtml).includes("<a "));
  assert.equal(safeUrl("HTTPS://example.test"), "HTTPS://example.test");
  assert.equal(safeUrl("data:text/html,x"), null);
  assert.equal(safeUrl(null), null);
});

test("bandHtml escapes the base and counts the cards", () => {
  const band = { base: "<b>Pony</b>", fits: true, entries: [{}, {}] };
  const html = bandHtml(band, { fits: true, fitTitle: "Made for <b>Pony</b>" }, escapeHtml);
  assert.ok(!html.includes("<b>"));
  assert.ok(html.includes('data-g-band="&lt;b&gt;Pony&lt;/b&gt;"'));
  assert.ok(html.includes('data-workflow="true"'));
  assert.ok(html.includes("2 LoRAs"));
  assert.ok(bandHtml({ base: "x", entries: [{}] }, {}, escapeHtml).includes("1 LoRA<"));
  assert.ok(
    bandHtml({ base: "x", entries: [{}] }, { noun: "model" }, escapeHtml).includes("1 model<")
  );
});

test("bandHtml says for this workflow, never fits, and always keeps the marker", () => {
  const html = bandHtml({ base: "SDXL", entries: [] }, { fits: true }, escapeHtml);
  assert.ok(html.includes("for this workflow"));
  assert.ok(!html.includes("fits"));
  const quiet = bandHtml({ base: "SDXL", entries: [] }, { fits: false, quiet: true }, escapeHtml);
  assert.ok(quiet.includes('class="cu-g-fit"'));
  assert.ok(quiet.includes('data-workflow="false" data-quiet'));
});

test("bandHtml folds: data-folded, a button head, and a grid that stays empty", () => {
  const open = bandHtml({ base: "SDXL", entries: [{}] }, {}, escapeHtml);
  assert.ok(!open.includes("data-folded"));
  assert.ok(open.includes('aria-expanded="true"'));
  const folded = bandHtml({ base: "SDXL", entries: [{}] }, { folded: true }, escapeHtml);
  assert.ok(folded.includes(" data-folded"));
  assert.ok(folded.includes('aria-expanded="false"'));
  assert.ok(/<button[^>]*data-g-fold/.test(folded));
  assert.ok(folded.endsWith('<div class="cu-g-grid"></div></section>'));
});

test("bandHtml with no base is one flat grid with no head", () => {
  const html = bandHtml({ base: null, entries: [{}, {}] }, {}, escapeHtml);
  assert.ok(!html.includes("cu-group-head"));
  assert.ok(html.includes('class="cu-g-grid"'));
});

test("cardHtml draws the mark with its label", () => {
  const on = cardHtml(model(), ui({ mark: "on" }), escapeHtml);
  assert.ok(on.includes('data-mark="on"'));
  assert.ok(on.includes('aria-label="In the prompt"'));
  const parked = cardHtml(model(), ui({ mark: "parked" }), escapeHtml);
  assert.ok(parked.includes('data-mark="parked"'));
  assert.ok(parked.includes('aria-label="Parked in the prompt"'));
  const none = cardHtml(model(), ui(), escapeHtml);
  assert.ok(!none.includes("data-mark"));
  assert.ok(!none.includes('aria-label="In the prompt"'));
  // The slot is there either way, so a mark arriving moves nothing.
  assert.ok(none.includes("data-g-mark"));
});

test("cardHtml draws a version chip only for a stack, beside the face", () => {
  const stacked = cardHtml(model(), ui({ versions: 3, versionLabel: "v<2>" }), escapeHtml);
  assert.ok(stacked.includes("3 versions ▾"));
  assert.ok(stacked.includes("data-g-vers"));
  assert.ok(stacked.includes("v&lt;2&gt;"));
  assert.ok(!stacked.includes("v<2>"));
  const face = stacked.slice(stacked.indexOf("<button"), stacked.indexOf("</button>"));
  assert.ok(!face.includes("data-g-vers"));
  assert.ok(!cardHtml(model(), ui({ versions: 1 }), escapeHtml).includes("data-g-vers"));
});

test("versionMenuHtml lists the versions with their dates and marks the shown one", () => {
  const html = versionMenuHtml(
    [
      { id: "a/<x>.safetensors", label: "v<2>", date: "2026-02-01", current: true },
      { id: "a/y.safetensors", label: "v1", date: "", current: false },
    ],
    escapeHtml
  );
  assert.equal(html.match(/<button/g).length, 2);
  assert.ok(html.includes('data-g-ver="a/&lt;x&gt;.safetensors"'));
  assert.ok(html.includes("v&lt;2&gt;"));
  assert.ok(html.includes("2026-02-01"));
  assert.equal(html.match(/aria-current="true"/g).length, 1);
});

test("typeSwitchHtml offers the types that have files, with the current one selected", () => {
  const html = typeSwitchHtml(["loras", "embeddings"], "loras", escapeHtml);
  assert.equal(html.match(/<button/g).length, 2);
  assert.ok(/data-g-type="loras"[^>]*aria-pressed="true"[^>]*>LoRAs</.test(html));
  assert.ok(/data-g-type="embeddings"[^>]*aria-pressed="false"[^>]*>Embeddings</.test(html));
  assert.ok(!html.includes("Models"));
  assert.equal(typeSwitchHtml([], "loras", escapeHtml), "");
  // In their own order whatever order they come in.
  const all = typeSwitchHtml(["embeddings", "models", "loras"], "models", escapeHtml);
  assert.ok(all.indexOf("LoRAs") < all.indexOf("Models"));
  assert.ok(all.indexOf("Models") < all.indexOf("Embeddings"));
});

test("targetHtml is only a note, and only when no node can take a click", () => {
  assert.equal(targetHtml("loras", true, 1, escapeHtml), "");
  assert.equal(targetHtml("embeddings", true, 2, escapeHtml), "");
  assert.equal(targetHtml("loras", false, 2, escapeHtml), "Select a Prompt + LoRAs node");
  assert.equal(
    targetHtml("loras", false, 0, escapeHtml),
    "No Prompt + LoRAs node in this workflow"
  );
  assert.equal(
    targetHtml("models", false, 3, escapeHtml),
    "Select a Load Diffusion Model or Load Checkpoint node"
  );
  assert.equal(targetHtml("models", false, 0, escapeHtml), "No model loader in this workflow");
  assert.equal(targetHtml("models", true, 1, escapeHtml), "");
  assert.equal(COPY.noPrompt, "No Prompt + LoRAs node in this workflow");
});

test("cardHtml dims a card that is not on this drive and disables its face", () => {
  const html = cardHtml(model({ missing: true }), ui(), escapeHtml);
  assert.ok(html.includes('class="cu-g-card" data-g-key'));
  assert.ok(html.includes(" data-missing"));
  assert.ok(html.includes('class="cu-g-pick"'));
  assert.ok(/<button[^>]*class="cu-g-pick"[^>]*aria-disabled="true"/.test(html));
  const onDisk = cardHtml(model(), ui(), escapeHtml);
  assert.ok(!onDisk.includes("data-missing"));
  assert.ok(!onDisk.includes("aria-disabled"));
});

test("cardHtml always has the action slot and the state overlay, so heights match", () => {
  for (const missing of [false, true]) {
    const html = cardHtml(model({ missing }), ui(), escapeHtml);
    assert.ok(html.includes('<span class="cu-g-act" data-g-act></span>'));
    assert.ok(
      html.includes('<span class="cu-g-over"><span class="cu-g-state" role="status"></span></span>')
    );
    // Siblings of the face: never inside the button.
    const face = html.slice(html.indexOf("<button"), html.indexOf("</button>"));
    assert.ok(!face.includes("data-g-act"));
    assert.ok(!face.includes("cu-g-over"));
  }
});

test("role=img is only on a mark span that shows a mark", () => {
  assert.ok(!cardHtml(model(), ui(), escapeHtml).includes('role="img"'));
  const on = cardHtml(model(), ui({ mark: "on" }), escapeHtml);
  assert.equal(on.split('role="img"').length - 1, 1);
  assert.match(on, /data-mark="on" role="img" aria-label=/);
});
