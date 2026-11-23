import assert from "node:assert/strict";
import { test } from "node:test";

import {
  infoHtml,
  menuHtml,
  moreMenuItems,
  savedArchiveFolder,
  toolbarHtml,
  updateToolbar,
  wireToolbar,
} from "../../web/panel_toolbar.js";

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

test("Filter leads in every tab; 🔍 and ⓘ sit at the right end", () => {
  const at = (html, name) => html.indexOf(`data-tb="${name}"`);
  for (const props of [
    base,
    { ...base, search: false },
    { ...base, search: false, info: false, sortLabel: null },
  ]) {
    const html = toolbarHtml(props, escapeHtml);
    const row = html.slice(0, html.indexOf("</div>"));
    assert.ok(
      row.indexOf("<button") ===
        row.indexOf('<button type="button" class="cu-tb-btn" data-tb="filter"')
    );
  }
  const all = toolbarHtml(base, escapeHtml);
  assert.ok(at(all, "filter") < at(all, "sort"));
  assert.ok(at(all, "sort") < all.indexOf('class="cu-tb-end"'));
  assert.ok(all.indexOf('class="cu-tb-end"') < at(all, "search"));
  assert.ok(at(all, "search") < at(all, "info"));
  const bare = toolbarHtml({ ...base, search: false, info: false }, escapeHtml);
  assert.ok(!bare.includes("cu-tb-end"));
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
  const count = (html) => html.split('aria-expanded="false"').length - 1;
  assert.equal(count(toolbarHtml({ ...base, info: false }, escapeHtml)), 2);
  assert.equal(count(toolbarHtml(base, escapeHtml)), 3);
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

test("a tab with no sort and no search has neither button, field nor slot", () => {
  const html = toolbarHtml(
    { query: "", filtered: 0, sortLabel: null, info: false, search: false },
    escapeHtml
  );
  assert.ok(html.includes('data-tb="filter"'));
  for (const gone of ['data-tb="sort"', 'data-tb="search"', 'data-tb="field"', 'data-tb="info"']) {
    assert.ok(!html.includes(gone), gone);
  }
  assert.ok(html.includes('data-tb-menu="filter"'));
  assert.ok(!html.includes('data-tb-menu="sort"'));
});

test("the info slot exists only with the info button", () => {
  assert.ok(toolbarHtml(base, escapeHtml).includes('data-tb-menu="info"'));
  assert.ok(!toolbarHtml({ ...base, info: false }, escapeHtml).includes('data-tb-menu="info"'));
});

test("a checks section can offer Select all and Deselect all", () => {
  const section = (actions) => ({
    kind: "checks",
    key: "types",
    title: "Types",
    options: [{ value: "a", label: "A", checked: true }],
    actions,
  });
  const html = menuHtml([section(true)], escapeHtml);
  assert.ok(html.includes('data-tb-all="types"'));
  assert.ok(html.includes('data-tb-none="types"'));
  assert.ok(!menuHtml([section(false)], escapeHtml).includes("data-tb-all"));
  const empty = menuHtml([{ ...section(false), options: [] }], escapeHtml);
  assert.ok(empty.includes("No options"));
});

test("infoHtml lists lines, skips empty ones, escapes, and offers a button", () => {
  const html = infoHtml(
    ["Last checked 3 minutes ago", "", "<b>"],
    { label: "Check again", key: "recheck" },
    escapeHtml
  );
  assert.equal(html.split("cu-tb-line").length - 1, 2);
  assert.ok(html.includes("&lt;b&gt;") && !html.includes("<b>"));
  assert.match(html, /data-tb-act="recheck"[^>]*>Check again</);
  assert.doesNotMatch(html, /data-tb-act="recheck"[^>]* disabled/);
  assert.match(
    infoHtml([], { label: "Go", key: "go", disabled: true }, escapeHtml),
    /data-tb-act="go" disabled/
  );
  assert.ok(!infoHtml(["x"], null, escapeHtml).includes("<button"));
});

test("updateToolbar leaves the field and menus and patches the labels", () => {
  const made = [];
  const node = (html) => {
    const attrs = {};
    const parts = {};
    const el = {
      innerHTML: html || "",
      getAttribute: (k) => (k in attrs ? attrs[k] : null),
      setAttribute: (k, v) => (attrs[k] = v),
      hasAttribute: (k) => k in attrs,
      toggleAttribute(k, on) {
        if (on) attrs[k] = "";
        else delete attrs[k];
      },
      parts,
      attrs,
    };
    made.push(el);
    return el;
  };
  const live = { search: node("🔍"), filter: node("Filter ▾"), sort: node("Sort · Name ▾") };
  const root = {
    ownerDocument: {
      createElement: () => {
        const holder = {
          set innerHTML(html) {
            const hasSearch = html.includes('data-tb="search"');
            holder.next = {
              search: hasSearch ? node(html.includes("cu-tb-query") ? "🔍glow" : "🔍") : null,
              filter: node(html.match(/data-tb="filter"[^>]*>([^<]*)</)[1]),
              sort: html.includes('data-tb="sort"')
                ? node(html.match(/data-tb="sort"[^>]*>([^<]*)</)[1])
                : null,
            };
            if (hasSearch && html.includes("data-active"))
              holder.next.search.attrs["data-active"] = "";
            if (hasSearch) {
              holder.next.search.attrs["aria-label"] = html.match(
                /data-tb="search"[^>]*aria-label="([^"]*)"/
              )[1];
            }
          },
          querySelector: (sel) => holder.next[sel.match(/data-tb="(\w+)"/)[1]],
        };
        return holder;
      },
    },
    querySelector: (sel) => live[sel.match(/data-tb="(\w+)"/)[1]],
  };
  updateToolbar(root, { query: "glow", filtered: 2, sortLabel: "Newest", info: true }, escapeHtml);
  assert.equal(live.search.innerHTML, "🔍glow");
  assert.ok("data-active" in live.search.attrs);
  assert.equal(live.search.attrs["aria-label"], "Search: glow");
  assert.equal(live.filter.innerHTML, "Filter · 2 ▾");
  assert.equal(live.sort.innerHTML, "Sort · Newest ▾");
  updateToolbar(root, { query: "", filtered: 0, sortLabel: "Newest", info: true }, escapeHtml);
  assert.ok(!("data-active" in live.search.attrs));
  assert.equal(live.search.innerHTML, "🔍");
});

test("the info button opens its popover like Filter and Sort", () => {
  const f = fakeRoot();
  const infoMenu = { hidden: true, attrs: {}, dataset: { tbMenu: "info" }, setAttribute() {} };
  const opened = [];
  f.root.querySelector = (sel) => (sel === '[data-tb-menu="info"]' ? infoMenu : null);
  f.root.querySelectorAll = () => [infoMenu];
  wireToolbar(f.root, { onOpen: (name) => opened.push(name) });
  f.click("info");
  assert.equal(infoMenu.hidden, false);
  assert.deepEqual(opened, ["info"]);
});

test("the ⋯ menu always offers Fetch Missing Metadata and offers the archive check only with a folder", () => {
  assert.deepEqual(
    moreMenuItems({ archiveFolder: "", busy: false }).map((i) => i.key),
    ["scan"]
  );
  assert.deepEqual(
    moreMenuItems({ archiveFolder: "  ", busy: false }).map((i) => i.key),
    ["scan"]
  );
  const items = moreMenuItems({ archiveFolder: "D:\old", busy: false });
  assert.deepEqual(
    items.map((i) => [i.key, i.label, i.disabled]),
    [
      ["scan", "Fetch Missing Metadata", false],
      ["archive", "Check with archive", false],
    ]
  );
});

test("a running job disables every ⋯ menu item", () => {
  const items = moreMenuItems({ archiveFolder: "D:\old", busy: true });
  assert.equal(items.length, 2);
  assert.ok(items.every((i) => i.disabled));
});

test("the ⋯ menu offers the archive check for the folder the server kept, not the one typed", () => {
  const keys = (answer) =>
    moreMenuItems({ archiveFolder: savedArchiveFolder(answer), busy: false }).map((i) => i.key);
  assert.deepEqual(keys({ config: { archiveFolder: "Z:\\models" }, pathWarnings: [] }), [
    "scan",
    "archive",
  ]);
  // A refused folder is left out: the answer holds the stored one, here none.
  const refused = {
    config: { archiveFolder: "" },
    pathWarnings: ["Archive folder D:\\models was not saved: it overlaps a model folder."],
  };
  assert.deepEqual(keys(refused), ["scan"]);
  assert.deepEqual(keys({}), ["scan"]);
  assert.deepEqual(keys(null), ["scan"]);
  assert.equal(savedArchiveFolder({ config: { archiveFolder: "  Z:\\models " } }), "Z:\\models");
});
