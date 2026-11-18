import assert from "node:assert/strict";
import test from "node:test";

import { insertIntoNode, pickTarget } from "../../web/prompt_target.js";

const nodes = (...ids) => ids.map((id) => ({ id, type: "Warp Lora Prompt" }));
const at = (list, id) => list.find((n) => n.id === id);

test("pickTarget prefers selection, then last used", () => {
  const all = nodes(1, 2, 3);
  assert.equal(pickTarget(all, [2], at(all, 3)).id, 2);
  assert.equal(pickTarget(all, [], at(all, 3)).id, 3);
});

test("pickTarget ignores a stale last-used id", () => {
  assert.equal(pickTarget(nodes(1, 2), [], nodes(9)[0]), null);
  const one = nodes(1);
  assert.equal(pickTarget(one, [], nodes(9)[0]).id, 1);
});

test("pickTarget ignores selected non-prompt nodes", () => {
  const all3 = nodes(1, 2, 3);
  assert.equal(pickTarget(all3, [7], at(all3, 3)).id, 3);
});

test("insertIntoNode uses _warppipeAddLora", () => {
  const seen = [];
  const node = {
    _warppipeAddLora: (stem) => {
      seen.push(stem);
      return true;
    },
  };
  assert.equal(insertIntoNode(node, "x"), true);
  assert.deepEqual(seen, ["x"]);
});

test("insertIntoNode falls back to the text widget", () => {
  const node = { widgets: [{ name: "text", value: "a" }] };
  assert.equal(insertIntoNode(node, "x"), true);
  assert.ok(node.widgets[0].value.includes("<lora:x:1.0>"));
});

test("pickTarget never picks a different node with the same id", () => {
  const workflowA = nodes(1, 3);
  const remembered = at(workflowA, 3);
  const workflowB = nodes(1, 3);
  assert.equal(pickTarget(workflowB, [], remembered), null);
});

test("insertIntoNode reports false when the node can't add", () => {
  assert.equal(insertIntoNode({ _warppipeAddLora: () => false }, "x"), false);
  assert.equal(insertIntoNode(null, "x"), false);
});
