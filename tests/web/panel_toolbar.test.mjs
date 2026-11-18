import assert from "node:assert/strict";
import { test } from "node:test";

import { menuHtml, toolbarHtml, wireToolbar } from "../../web/panel_toolbar.js";

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
  // The visible text is cut (the label and the field hold the whole query).
  const shown = html.match(/<span class="cu-tb-query">([^<]*)<\/span>/)[1];
  assert.ok(!shown.includes("abcdefghijklmnopqrs"));
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

test("the search field is hidden unless open", () => {
  assert.match(toolbarHtml(base, escapeHtml), /data-tb="field"[^>]* hidden>/);
  assert.doesNotMatch(
    toolbarHtml({ ...base, open: true }, escapeHtml),
    /data-tb="field"[^>]* hidden/
  );
});

test("two menus with the same key get different radio names", () => {
  const sections = [
    {
      kind: "choice",
      key: "sort",
      title: "Sort",
      options: [{ value: "a", label: "A" }],
      value: "a",
    },
  ];
  const name = (html) => html.match(/name="([^"]+)"/)[1];
  assert.notEqual(name(menuHtml(sections, escapeHtml)), name(menuHtml(sections, escapeHtml)));
  assert.match(menuHtml(sections, escapeHtml), /data-key="sort" data-value="a"/);
});

test("the filter and sort buttons start collapsed", () => {
  const html = toolbarHtml(base, escapeHtml);
  assert.equal(html.split('aria-expanded="false"').length - 1, 2);
});

// A minimal fake DOM: recording listeners and just the elements wireToolbar reads.
function fakeRoot() {
  const added = [];
  const removed = [];
  const el = (extra = {}) => ({
    hidden: true,
    attrs: {},
    setAttribute(k, v) {
      this.attrs[k] = v;
    },
    ...extra,
  });
  const doc = {
    addEventListener: (type, fn) => added.push([doc, type, fn]),
    removeEventListener: (type, fn) => removed.push([doc, type, fn]),
  };
  const filterBtn = el();
  const filterMenu = el({ dataset: { tbMenu: "filter" } });
  const sortMenu = el({ dataset: { tbMenu: "sort" } });
  const root = {
    ownerDocument: doc,
    addEventListener: (type, fn) => added.push([root, type, fn]),
    removeEventListener: (type, fn) => removed.push([root, type, fn]),
    contains: () => true,
    querySelector: (sel) =>
      sel === '[data-tb="filter"]'
        ? filterBtn
        : sel === '[data-tb-menu="filter"]'
          ? filterMenu
          : null,
    querySelectorAll: () => [filterMenu, sortMenu],
  };
  const click = (name) =>
    added
      .filter(([target, type]) => target === root && type === "click")
      .forEach(([, , fn]) => fn({ target: { closest: () => ({ dataset: { tb: name } }) } }));
  return { root, added, removed, filterBtn, filterMenu, click };
}

test("wiring the same root twice removes the first wiring", () => {
  const f = fakeRoot();
  wireToolbar(f.root, {});
  const first = f.added.slice();
  assert.equal(f.removed.length, 0);
  wireToolbar(f.root, {});
  assert.equal(f.removed.length, first.length);
  for (const entry of first) assert.ok(f.removed.some((r) => r.every((v, i) => v === entry[i])));
});

test("destroy removes every listener it added", () => {
  const f = fakeRoot();
  wireToolbar(f.root, {}).destroy();
  assert.ok(f.added.length >= 5);
  assert.equal(f.removed.length, f.added.length);
  for (const entry of f.added) assert.ok(f.removed.some((r) => r.every((v, i) => v === entry[i])));
});

test("aria-expanded follows the menu open and closed", () => {
  const f = fakeRoot();
  const opened = [];
  const wiring = wireToolbar(f.root, { onOpen: (name) => opened.push(name) });
  f.click("filter");
  assert.equal(f.filterMenu.hidden, false);
  assert.equal(f.filterBtn.attrs["aria-expanded"], "true");
  assert.deepEqual(opened, ["filter"]);
  wiring.close();
  assert.equal(f.filterMenu.hidden, true);
  assert.equal(f.filterBtn.attrs["aria-expanded"], "false");
});

test("the search button's label names the active query", () => {
  const html = toolbarHtml({ ...base, query: "glow" }, escapeHtml);
  assert.match(html, /data-tb="search"[^>]*aria-label="Search: glow"/);
  assert.match(toolbarHtml(base, escapeHtml), /data-tb="search"[^>]*aria-label="Search"/);
  const hostile = toolbarHtml({ ...base, query: '"><b>' }, escapeHtml);
  assert.ok(!hostile.includes("<b>"));
});

test("onOpen throwing still opens the menu", () => {
  const f = fakeRoot();
  const warn = console.warn;
  console.warn = () => {};
  try {
    wireToolbar(f.root, {
      onOpen: () => {
        throw new Error("boom");
      },
    });
    f.click("filter");
  } finally {
    console.warn = warn;
  }
  assert.equal(f.filterMenu.hidden, false);
  assert.equal(f.filterBtn.attrs["aria-expanded"], "true");
});

// A fake with a search field and button, to see where focus goes.
function fieldRoot() {
  const added = [];
  const doc = { addEventListener: (t, fn) => added.push([doc, t, fn]), removeEventListener() {} };
  const field = { hidden: false, value: "", focused: false, focus() {} };
  const button = {
    focused: 0,
    focus() {
      this.focused += 1;
    },
  };
  const root = {
    ownerDocument: doc,
    addEventListener: (t, fn) => added.push([root, t, fn]),
    removeEventListener() {},
    contains: () => true,
    querySelector: (sel) =>
      sel === '[data-tb="field"]' ? field : sel === '[data-tb="search"]' ? button : null,
    querySelectorAll: () => [],
  };
  const fire = (target, type, event) =>
    added.filter(([t, ty]) => t === target && ty === type).forEach(([, , fn]) => fn(event));
  return { root, doc, field, button, fire };
}

test("Esc closes the field and returns focus to the search button", () => {
  const f = fieldRoot();
  wireToolbar(f.root, {});
  f.fire(f.doc, "keydown", { key: "Escape" });
  assert.equal(f.field.hidden, true);
  assert.equal(f.button.focused, 1);
});

test("an empty blur returns focus to the button unless focus moved to another control", () => {
  const f = fieldRoot();
  wireToolbar(f.root, {});
  f.fire(f.root, "focusout", { target: f.field, relatedTarget: {} });
  assert.equal(f.field.hidden, true);
  assert.equal(f.button.focused, 0);
  f.field.hidden = false;
  f.fire(f.root, "focusout", { target: f.field, relatedTarget: null });
  assert.equal(f.field.hidden, true);
  assert.equal(f.button.focused, 1);
});
