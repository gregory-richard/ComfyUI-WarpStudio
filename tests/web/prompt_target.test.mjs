import assert from "node:assert/strict";
import test from "node:test";

import {
  OPEN_GALLERY,
  currentTarget,
  addEmbeddingToNode,
  currentLoader,
  loadedModel,
  noteLoaderUsed,
  cycleInNode,
  markIn,
  sameModelName,
  setLoaderModel,
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

test("pickTarget never picks a different node with the same id", () => {
  const workflowA = nodes(1, 3);
  const remembered = at(workflowA, 3);
  const workflowB = nodes(1, 3);
  assert.equal(pickTarget(workflowB, [], remembered), null);
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

const widgetsOf = (text, loras) => [
  { name: "text", value: text },
  { name: "loras", value: loras },
];

test("cycleInNode without hooks cycles the loras widget: absent, on, parked", () => {
  const node = { widgets: widgetsOf("a cat", "") };
  assert.equal(cycleInNode(node, "maker - glow (krea2)"), "on");
  assert.equal(node.widgets[1].value, "<lora:maker - glow (krea2):1.00>");
  assert.equal(cycleInNode(node, "maker - glow (krea2)"), "parked");
  assert.equal(node.widgets[1].value, "// <lora:maker - glow (krea2):1.00>");
  assert.equal(node.widgets[0].value, "a cat");
  assert.equal(cycleInNode({ widgets: [] }, "x"), null);
  assert.equal(cycleInNode(null, "x"), null);
});

test("cycleInNode prefers _warppipeCycleLora and returns its answer", () => {
  const seen = [];
  const node = {
    widgets: widgetsOf("", ""),
    _warppipeCycleLora: (stem) => {
      seen.push(stem);
      return "parked";
    },
  };
  assert.equal(cycleInNode(node, "x"), "parked");
  assert.deepEqual(seen, ["x"]);
  assert.equal(node.widgets[1].value, "");
  assert.equal(cycleInNode({ _warppipeCycleLora: () => null }, "x"), null);
});

test("markIn reads _warppipeListText when present", () => {
  const node = {
    widgets: widgetsOf("", "<lora:a:1>"),
    _warppipeListText: () => "// <lora:a:1>",
  };
  assert.equal(markIn(node, "a"), "parked");
  assert.equal(markIn({ widgets: widgetsOf("", "<lora:a:1>") }, "a"), "on");
  assert.equal(markIn({ widgets: widgetsOf("", "") }, "a"), null);
});

test("addEmbeddingToNode without hooks edits the text widget", () => {
  const node = { widgets: widgetsOf("a cat", "") };
  assert.equal(addEmbeddingToNode(node, "fine"), true);
  assert.equal(node.widgets[0].value, "a cat, embedding:fine");
  assert.equal(addEmbeddingToNode(node, "fine"), false);
  assert.equal(addEmbeddingToNode({ _warppipeAddEmbedding: () => true }, "fine"), true);
});

const loader = (id, type, widget, values) => ({
  id,
  type,
  widgets: [{ name: widget, value: values[0], options: { values } }],
});

test("currentLoader picks the selected UNETLoader over the last used one", () => {
  const first = loader(1, "UNETLoader", "unet_name", ["a.safetensors"]);
  const second = loader(2, "UNETLoader", "unet_name", ["b.safetensors"]);
  const ckpt = loader(3, "CheckpointLoaderSimple", "ckpt_name", ["c.safetensors"]);
  const graph = { _nodes: [first, second, ckpt] };
  noteLoaderUsed(first);
  const app = { graph, canvas: canvasOn(graph, [second]) };
  assert.equal(currentLoader(app, "diffusion_models"), second);
  app.canvas.selectNode(null);
  assert.equal(currentLoader(app, "diffusion_models"), first);
  // A checkpoint loader is not a candidate for diffusion models.
  app.canvas.selectNode(ckpt);
  assert.equal(currentLoader(app, "diffusion_models"), first);
  assert.equal(currentLoader(app, "checkpoints"), ckpt);
});

test("setLoaderModel refuses a name that is not an option", () => {
  const node = loader(1, "UNETLoader", "unet_name", ["a.safetensors", "b.safetensors"]);
  const called = [];
  node.widgets[0].callback = (v) => called.push(v);
  assert.equal(setLoaderModel(node, "nope.safetensors"), false);
  assert.equal(node.widgets[0].value, "a.safetensors");
  assert.equal(setLoaderModel(node, "b.safetensors"), true);
  assert.equal(node.widgets[0].value, "b.safetensors");
  assert.deepEqual(called, ["b.safetensors"]);
  assert.equal(loadedModel(node), "b.safetensors");
  assert.equal(setLoaderModel({ type: "KSampler", widgets: [] }, "x"), false);
});

test("setLoaderModel marks the canvas dirty even when the callback throws", () => {
  const node = loader(1, "UNETLoader", "unet_name", ["a.safetensors", "b.safetensors"]);
  const canvas = { id: "canvas" };
  let dirty = 0;
  let args = null;
  node.setDirtyCanvas = () => dirty++;
  node.widgets[0].callback = (...got) => {
    args = got;
    throw new Error("boom");
  };
  assert.throws(() => setLoaderModel(node, "b.safetensors", canvas), /boom/);
  assert.equal(dirty, 1);
  assert.deepEqual(args, ["b.safetensors", canvas, node]);
  assert.equal(node.widgets[0].value, "b.safetensors");
});

test("setLoaderModel reads the canvas from the node's graph when none is passed", () => {
  const node = loader(1, "UNETLoader", "unet_name", ["a.safetensors"]);
  const canvas = {};
  node.graph = { canvas };
  let got = null;
  node.widgets[0].callback = (_name, c) => (got = c);
  setLoaderModel(node, "a.safetensors");
  assert.equal(got, canvas);
});

test("setLoaderModel finds a name the loader lists with backslashes", () => {
  const node = loader(1, "UNETLoader", "unet_name", ["sub\\Maker - Net.safetensors"]);
  assert.equal(setLoaderModel(node, "sub/maker - net.safetensors"), true);
  assert.equal(node.widgets[0].value, "sub\\Maker - Net.safetensors");
  assert.equal(sameModelName(loadedModel(node), "sub/Maker - Net.safetensors"), true);
  assert.equal(sameModelName("a/b", "a/c"), false);
});
