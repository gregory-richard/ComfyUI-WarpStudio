import assert from "node:assert/strict";
import { test } from "node:test";

import { menuHtml, toolbarHtml } from "../../web/panel_toolbar.js";

const escapeHtml = (text) =>
  String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const base = { query: "", filtered: 0, sortLabel: "Name", info: true };

test("a query marks the search button active and shows the query", () => {
  const html = toolbarHtml({ ...base, query: "glow" }, escapeHtml);
  assert.match(html, /data-tb="search"[^>]*data-active/);
  assert.ok(html.includes("glow"));
  assert.ok(!toolbarHtml(base, escapeHtml).includes("data-active"));
});

test("a long query is cut to 18 characters with an ellipsis", () => {
  const html = toolbarHtml({ ...base, query: "abcdefghijklmnopqrstuvwxyz" }, escapeHtml);
  assert.ok(html.includes("abcdefghijklmnopqr…"));
  // The button is cut; the field still holds the whole query.
  const button = html.slice(0, html.indexOf("</button>"));
  assert.ok(!button.includes("abcdefghijklmnopqrs"));
});

test("the filter label counts active filters", () => {
  assert.ok(toolbarHtml({ ...base, filtered: 2 }, escapeHtml).includes("Filter · 2"));
  assert.ok(!toolbarHtml(base, escapeHtml).includes("Filter ·"));
});

test("the sort label names the sort", () => {
  assert.ok(toolbarHtml({ ...base, sortLabel: "Newest" }, escapeHtml).includes("Sort · Newest"));
});

test("info false leaves out the info button", () => {
  assert.ok(!toolbarHtml({ ...base, info: false }, escapeHtml).includes('data-tb="info"'));
  assert.ok(toolbarHtml(base, escapeHtml).includes('data-tb="info"'));
});

test("toolbarHtml escapes every user string", () => {
  const html = toolbarHtml({ ...base, query: "<b>", sortLabel: "<b>" }, escapeHtml);
  assert.ok(html.includes("&lt;b&gt;"));
  assert.ok(!html.includes("<b>"));
});

test("menuHtml renders checks, a choice and a toggle", () => {
  const html = menuHtml(
    [
      {
        kind: "checks",
        key: "base",
        title: "Base",
        options: [
          { value: "pony", label: "Pony", checked: true },
          { value: "krea2", label: "Krea 2", checked: false },
        ],
      },
      {
        kind: "choice",
        key: "sort",
        title: "Sort",
        options: [
          { value: "name", label: "Name" },
          { value: "new", label: "Newest" },
        ],
        value: "new",
      },
      { kind: "toggle", key: "quiet", label: "Quiet", checked: true },
    ],
    escapeHtml
  );
  assert.ok(html.includes("cu-filter-panel"));
  assert.match(html, /data-value="pony"[^>]*checked/);
  assert.doesNotMatch(html, /data-value="krea2"[^>]*checked/);
  assert.match(html, /data-value="new"[^>]*checked/);
  assert.doesNotMatch(html, /data-value="name"[^>]*checked/);
  assert.match(html, /data-key="quiet"[^>]*checked/);
});

test("menuHtml escapes titles, labels and values", () => {
  const html = menuHtml(
    [
      {
        kind: "checks",
        key: "<b>",
        title: "<b>",
        options: [{ value: "<b>", label: "<b>", checked: false }],
      },
      { kind: "toggle", key: "<b>", label: "<b>", checked: false },
    ],
    escapeHtml
  );
  assert.ok(!html.includes("<b>"));
  assert.ok(html.includes("&lt;b&gt;"));
});
