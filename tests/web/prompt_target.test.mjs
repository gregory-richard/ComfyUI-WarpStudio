import assert from "node:assert/strict";
import test from "node:test";

import {
  OPEN_GALLERY,
  currentTarget,
  insertIntoNode,
  pickTarget,
  promptNodesOf,
  requestGallery,
  targetLabel,
} from "../../web/prompt_target.js";

const nodes = (...ids) => ids.map((id) => ({ id, type: "Warp Lora Prompt" }));
const at = (list, id) => list.find((n) => n.id === id);

// A canvas the way frontend 1.58.5 has it: the graph on screen, the selection
// as a Set of node objects, and selectNode replacing the selection.
const canvasOn = (graph, selected = []) => {
  const canvas = { graph, selectedItems: new Set(selected) };
  canvas.selectNode = (node) => {
    canvas.selectedItems = new Set(node ? [node] : []);
  };
  return canvas;
};

test("pickTarget prefers selection, then last used", () => {
  const all = nodes(1, 2, 3);
  assert.equal(pickTarget(all, [at(all, 2)], at(all, 3)).id, 2);
  assert.equal(pickTarget(all, [], at(all, 3)).id, 3);
});

test("pickTarget ignores a stale last-used id", () => {
  assert.equal(pickTarget(nodes(1, 2), [], nodes(9)[0]), null);
  const one = nodes(1);
  assert.equal(pickTarget(one, [], nodes(9)[0]).id, 1);
});

test("pickTarget ignores selected non-prompt nodes", () => {
  const all3 = nodes(1, 2, 3);
  assert.equal(pickTarget(all3, [{ id: 7, type: "KSampler" }], at(all3, 3)).id, 3);
});

test("pickTarget matches the selection by identity, never by id", () => {
  const root = nodes(1, 2);
  const inSubgraph = nodes(1)[0]; // same id as a root node, another node
  assert.equal(pickTarget(root, [inSubgraph], null), null);
  assert.equal(pickTarget(root, [inSubgraph], at(root, 2)), at(root, 2));
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

test("currentTarget reads the graph on screen, not the root", () => {
  const root = { _nodes: nodes(1, 2) };
  const sub = { _nodes: [...nodes(1), { id: 4, type: "KSampler" }] };
  const app = { graph: root, canvas: canvasOn(sub) };
  // The subgraph's only prompt node, though its id is a root node's too.
  assert.equal(currentTarget(app), sub._nodes[0]);
  assert.deepEqual(promptNodesOf(app), [sub._nodes[0]]);
  // Back at the root: the canvas shows the root graph again.
  app.canvas.graph = root;
  assert.deepEqual(promptNodesOf(app), root._nodes);
});

test("currentTarget: a selected subgraph node sharing a root node's id is the one picked", () => {
  const root = { _nodes: nodes(1, 2) };
  const sub = { _nodes: nodes(1, 3) };
  const selected = sub._nodes[0];
  const app = { graph: root, canvas: canvasOn(sub, [selected]) };
  assert.equal(currentTarget(app), selected);
  assert.notEqual(currentTarget(app), root._nodes[0]);
});

test("currentTarget falls back to app.graph and to selected_nodes", () => {
  const graph = { nodes: nodes(1, 2) };
  const app = { graph, canvas: { selected_nodes: { 2: graph.nodes[1] } } };
  assert.equal(currentTarget(app), graph.nodes[1]);
  assert.equal(promptNodesOf({}).length, 0);
  assert.equal(currentTarget({}), null);
});

test("currentTarget never reads the deprecated selected_nodes when selectedItems is there", () => {
  const graph = { _nodes: nodes(1, 2) };
  const canvas = canvasOn(graph, [graph._nodes[0]]);
  Object.defineProperty(canvas, "selected_nodes", {
    get() {
      throw new Error("selected_nodes was read");
    },
  });
  assert.equal(currentTarget({ graph, canvas }), graph._nodes[0]);
});

test("Browse LoRAs makes its node the target over one selected before", () => {
  const graph = { _nodes: nodes(1, 2) };
  const [a, b] = graph._nodes;
  const app = { graph, canvas: canvasOn(graph, [a]) };
  assert.equal(currentTarget(app), a); // typing in A left it selected
  const events = new EventTarget();
  const seen = [];
  events.addEventListener(OPEN_GALLERY, (ev) => seen.push(ev.detail));
  requestGallery(app, b, events);
  assert.equal(currentTarget(app), b);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].node, b);
  assert.equal(seen[0].nodeId, 2);
});

test("Browse LoRAs still asks for the gallery on a canvas without selectNode", () => {
  const events = new EventTarget();
  let detail = null;
  events.addEventListener(OPEN_GALLERY, (ev) => (detail = ev.detail));
  const node = nodes(5)[0];
  requestGallery({ canvas: {} }, node, events);
  assert.deepEqual(detail, { node, nodeId: 5 });
});

test("targetLabel tells same-titled nodes apart by id", () => {
  const twins = [
    { id: 3, title: "Prompt + LoRAs" },
    { id: 8, title: "Prompt + LoRAs" },
    { id: 9, title: "Hero shot" },
  ];
  assert.equal(targetLabel(twins[0], twins), "Prompt + LoRAs · #3");
  assert.equal(targetLabel(twins[1], twins), "Prompt + LoRAs · #8");
  assert.equal(targetLabel(twins[2], twins), "Hero shot");
  // Untitled nodes read as the default title, so two of them are twins too.
  const untitled = [{ id: 1 }, { id: 2, title: "" }];
  assert.equal(targetLabel(untitled[1], untitled), "Prompt + LoRAs · #2");
  // Alone, or without the list: just the title.
  assert.equal(targetLabel(twins[0], [twins[0]]), "Prompt + LoRAs");
  assert.equal(targetLabel(twins[0]), "Prompt + LoRAs");
});
