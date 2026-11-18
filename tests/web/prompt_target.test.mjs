import assert from "node:assert/strict";
import test from "node:test";

import { insertIntoNode, pickTarget } from "../../web/prompt_target.js";

const nodes = (...ids) => ids.map((id) => ({ id, type: "Warp Lora Prompt" }));

test("pickTarget prefers selection, then last used", () => {
  const all = nodes(1, 2, 3);
  assert.equal(pickTarget(all, [2], 3).id, 2);
  assert.equal(pickTarget(all, [], 3).id, 3);
});

test("pickTarget ignores a stale last-used id", () => {
  assert.equal(pickTarget(nodes(1, 2), [], 9), null);
  assert.equal(pickTarget(nodes(1), [], 9).id, 1);
});

test("pickTarget ignores selected non-prompt nodes", () => {
  assert.equal(pickTarget(nodes(1, 2, 3), [7], 3).id, 3);
});

test("insertIntoNode uses _warppipeAddLora", () => {
  const seen = [];
  const node = { _warppipeAddLora: (stem) => seen.push(stem) };
  assert.equal(insertIntoNode(node, "x"), true);
  assert.deepEqual(seen, ["x"]);
});

test("insertIntoNode falls back to the text widget", () => {
  const node = { widgets: [{ name: "text", value: "a" }] };
  assert.equal(insertIntoNode(node, "x"), true);
  assert.ok(node.widgets[0].value.includes("<lora:x:1.0>"));
});
