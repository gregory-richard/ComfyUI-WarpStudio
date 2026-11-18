import assert from "node:assert/strict";
import { test } from "node:test";

import { cardModel } from "../../web/gallery_core.js";
import { COPY, bandHtml, cardHtml, safeUrl, targetHtml } from "../../web/gallery_view.js";

// The panel's escapeHtml, as it is.
const escapeHtml = (text) =>
  String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const model = (fields) => ({
  key: "pony/maker - glow - v1 (pony).safetensors",
  title: "Glow",
  creator: "maker",
  detail: "v1 · Pony",
  triggers: 0,
  url: null,
  thumbnail: null,
  stem: "maker - glow - v1 (pony)",
  ...fields,
});

test("cardHtml escapes an HTML title", () => {
  const html = cardHtml(model({ title: "<img src=x onerror=1>" }), escapeHtml);
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
    escapeHtml
  );
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes(evil));
  // key, stem (the tooltip), thumbnail, creator, detail and url: six escaped copies.
  assert.equal(html.split("&quot;&gt;&lt;script&gt;").length - 1, 6);
});

test("cardHtml keeps an empty creator row, so heights match", () => {
  const html = cardHtml(model({ creator: "" }), escapeHtml);
  assert.ok(html.includes('<span class="cu-g-creator"></span>'));
});

test("cardHtml keeps every row from cardModel's gaps", () => {
  const html = cardHtml(
    cardModel({ id: "misc/amber.safetensors", folder: "misc", name: "Amber" }),
    escapeHtml
  );
  assert.ok(html.includes('<span class="cu-g-creator"></span>'));
  assert.ok(html.includes('<span class="cu-g-title">Amber</span>'));
  assert.ok(html.includes('<span class="cu-g-detail">misc</span>'));
});

test("cardHtml shows a lazy thumbnail, or the stem without one", () => {
  const withImage = cardHtml(model({ thumbnail: "/t?a&b" }), escapeHtml);
  assert.ok(withImage.includes('<img src="/t?a&amp;b" alt="" loading="lazy"'));
  assert.ok(!withImage.includes("cu-g-noimg"));
  const without = cardHtml(model(), escapeHtml);
  assert.ok(without.includes('<span class="cu-g-noimg">maker - glow - v1 (pony)</span>'));
});

test("cardHtml has one button for the face, and the link beside it", () => {
  const html = cardHtml(model({ url: "https://example.test/models/1" }), escapeHtml);
  assert.equal(html.match(/<button/g).length, 1);
  assert.ok(html.indexOf("</button>") < html.indexOf("<a "));
  assert.ok(html.includes('href="https://example.test/models/1"'));
  assert.ok(html.includes('target="_blank" rel="noopener noreferrer"'));
});

test("cardHtml shows the badge only with trigger words", () => {
  assert.ok(!cardHtml(model(), escapeHtml).includes("cu-g-badge"));
  const html = cardHtml(model({ triggers: 3 }), escapeHtml);
  assert.ok(html.includes('title="3 trigger words"'));
  assert.ok(html.includes("⊕ 3</span>"));
});

test("cardHtml links only to a web page", () => {
  assert.ok(!cardHtml(model(), escapeHtml).includes("<a "));
  assert.ok(!cardHtml(model({ url: "javascript:alert(1)" }), escapeHtml).includes("<a "));
  assert.equal(safeUrl("HTTPS://example.test"), "HTTPS://example.test");
  assert.equal(safeUrl("data:text/html,x"), null);
  assert.equal(safeUrl(null), null);
});

test("bandHtml escapes the base and counts the LoRAs", () => {
  const band = { base: "<b>Pony</b>", fits: true, entries: [{}, {}] };
  const html = bandHtml(band, { fits: true, fitTitle: "Made for <b>Pony</b>" }, escapeHtml);
  assert.ok(!html.includes("<b>"));
  assert.ok(html.includes('data-g-band="&lt;b&gt;Pony&lt;/b&gt;"'));
  assert.ok(html.includes('data-fits="true"'));
  assert.ok(html.includes("2 LoRAs"));
  assert.ok(bandHtml({ base: "x", entries: [{}] }, {}, escapeHtml).includes("1 LoRA<"));
});

test("bandHtml always has the fit marker, so marking never moves the band", () => {
  const html = bandHtml({ base: "SDXL", entries: [] }, { fits: false, quiet: true }, escapeHtml);
  assert.ok(html.includes('class="cu-g-fit"'));
  assert.ok(html.includes('data-fits="false" data-quiet'));
});

test("targetHtml names the node, or says why there is none", () => {
  const html = targetHtml({ title: "<i>Main</i>" }, 1, escapeHtml);
  assert.equal(html, 'Adds to: <span class="cu-g-node">&lt;i&gt;Main&lt;/i&gt;</span>');
  assert.equal(
    targetHtml({}, 1, escapeHtml),
    'Adds to: <span class="cu-g-node">Prompt + LoRAs</span>'
  );
  assert.equal(targetHtml(null, 0, escapeHtml), COPY.noNode);
  assert.equal(targetHtml(null, 2, escapeHtml), COPY.noChoice);
  assert.equal(COPY.noNode, "No Prompt + LoRAs node in this workflow");
});
