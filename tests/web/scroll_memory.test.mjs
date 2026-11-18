import test from "node:test";
import assert from "node:assert/strict";
import { createScrollMemory, findScroller } from "../../web/scroll_memory.js";

test("findScroller returns the nearest scrolling ancestor", () => {
  const outer = { parentElement: null, overflowY: "auto" };
  const mid = { parentElement: outer, overflowY: "visible" };
  const inner = { parentElement: mid, overflowY: "hidden" };
  const leaf = { parentElement: inner };
  assert.equal(findScroller(leaf, (n) => ({ overflowY: n.overflowY })), outer);
  assert.equal(findScroller({ parentElement: null }, () => ({ overflowY: "auto" })), null);
});

test("scroll memory keeps an offset per view and defaults to the top", () => {
  const mem = createScrollMemory();
  assert.equal(mem.get("gallery"), 0);
  mem.save("gallery", 420);
  mem.save("rename", 10);
  mem.save("gallery", Number.NaN);
  assert.equal(mem.get("gallery"), 420);
  assert.equal(mem.get("rename"), 10);
  assert.equal(mem.get("updates"), 0);
});
