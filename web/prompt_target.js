// Which Prompt + LoRAs node a LoRA goes to, when the request comes from the
// sidebar and there is no node under the cursor. Takes `app` as an argument so
// it imports nothing from ComfyUI.

import { addEmbedding, cycleLora, hasEmbedding, loraState } from "./prompt_marks.js";

export const PROMPT_NODE = "Warp Lora Prompt";

const TEXT_WIDGET = "text";
const LIST_WIDGET = "loras";

let lastUsedNode = null;

// The node object itself, not its id: ids are small integers that repeat from
// one workflow to the next, and a remembered 3 must not become another graph's 3.
export function noteUsed(node) {
  if (node) lastUsedNode = node;
}

export function lastUsed() {
  return lastUsedNode;
}

/** Selection first, then the last one used, then the only one there is.
 *
 * Every argument is node objects, matched by identity: a subgraph's node 3 is
 * not the root's node 3, and neither is the 3 of a workflow opened since.
 */
export function pickTarget(promptNodes, selectedNodes, lastUsedNode) {
  const nodes = promptNodes || [];
  const selected = new Set(selectedNodes || []);
  const picked = nodes.find((n) => selected.has(n));
  if (picked) return picked;
  if (lastUsedNode && nodes.includes(lastUsedNode)) return lastUsedNode;
  return nodes.length === 1 ? nodes[0] : null;
}

const isPrompt = (n) => n && (n.type === PROMPT_NODE || n.comfyClass === PROMPT_NODE);

/** The Prompt + LoRAs nodes of the graph on screen: an open subgraph's, else
 *  the workflow's. A node in a graph that is not shown is out of reach. */
export function promptNodesOf(app) {
  const graph = app?.canvas?.graph ?? app?.graph;
  const all = graph?._nodes ?? graph?.nodes ?? [];
  return Array.from(all).filter(isPrompt);
}

/* The selected nodes themselves. selectedItems is the current API; the
   deprecated selected_nodes is read only on a frontend without it. */
function selectionOf(app) {
  const canvas = app?.canvas;
  if (!canvas) return [];
  if (canvas.selectedItems) return [...canvas.selectedItems];
  return Object.values(canvas.selected_nodes || {});
}

export function currentTarget(app) {
  return pickTarget(promptNodesOf(app), selectionOf(app), lastUsed());
}

export const OPEN_GALLERY = "warpstudio:open-gallery";

/** The Browse LoRAs button: select its node, then ask the panel for the Gallery.
 *
 * A button click does not select its node on the canvas, so another prompt
 * node that was being typed in would stay selected and win the target. Selecting
 * it here also counts it as used (its onSelected) and moves the Gallery's line
 * (the canvas's onSelectionChange). The event carries the node itself.
 */
export function requestGallery(app, node, events = globalThis.window) {
  app?.canvas?.selectNode?.(node);
  events?.dispatchEvent(new CustomEvent(OPEN_GALLERY, { detail: { node, nodeId: node.id } }));
}

const widgetOf = (node, name) => (node?.widgets || []).find((w) => w.name === name);

/** The text of the list a click lands in: the node's hook, else its first list. */
function listText(node) {
  if (typeof node?._warppipeListText === "function") return node._warppipeListText();
  return String(widgetOf(node, LIST_WIDGET)?.value ?? "");
}

function promptText(node) {
  if (typeof node?._warppipePromptText === "function") return node._warppipePromptText();
  return String(widgetOf(node, TEXT_WIDGET)?.value ?? "");
}

/** One click on a LoRA: absent -> on, on -> parked, parked -> on.
 *  Returns the new state, or null when the node has no list to change. */
export function cycleInNode(node, stem) {
  if (!node) return null;
  if (typeof node._warppipeCycleLora === "function") {
    const state = node._warppipeCycleLora(stem);
    if (state) node.setDirtyCanvas?.(true, true);
    return state ?? null;
  }
  const widget = widgetOf(node, LIST_WIDGET);
  if (!widget) return null;
  const got = cycleLora(String(widget.value ?? ""), stem);
  widget.value = got.text;
  node.setDirtyCanvas?.(true, true);
  return got.state;
}

/** "on", "parked" or null: how the node's list holds this LoRA right now. */
export function markIn(node, stem) {
  if (!node) return null;
  return loraState(listText(node), stem);
}

/** Add an embedding to the prose. True when it was added, false when it was
 *  already there or the node has no prose box. */
export function addEmbeddingToNode(node, stem) {
  if (!node) return false;
  if (typeof node._warppipeAddEmbedding === "function") {
    const added = node._warppipeAddEmbedding(stem) === true;
    if (added) node.setDirtyCanvas?.(true, true);
    return added;
  }
  const widget = widgetOf(node, TEXT_WIDGET);
  if (!widget) return false;
  const before = String(widget.value ?? "");
  const next = addEmbedding(before, stem);
  if (next === before) return false;
  widget.value = next;
  node.setDirtyCanvas?.(true, true);
  return true;
}

export function embeddingIn(node, stem) {
  return node ? hasEmbedding(promptText(node), stem) : false;
}

export const PROMPT_CHANGED = "warpstudio:prompt-changed";

/* Loaders: the checkpoint and diffusion-model loaders a card can load into. */
export const LOADERS = {
  CheckpointLoaderSimple: { widget: "ckpt_name", kind: "checkpoints" },
  UNETLoader: { widget: "unet_name", kind: "diffusion_models" },
};

const loaderTypeOf = (node) => {
  const type = node?.type ?? node?.comfyClass;
  return Object.hasOwn(LOADERS, type) ? type : null;
};

let lastLoaderNode = null;

export function noteLoaderUsed(node) {
  if (node) lastLoaderNode = node;
}

/** The loaders of one kind in the graph on screen. */
export function loaderNodesOf(app, kind) {
  const graph = app?.canvas?.graph ?? app?.graph;
  const all = graph?._nodes ?? graph?.nodes ?? [];
  return Array.from(all).filter((n) => {
    const type = loaderTypeOf(n);
    return type && LOADERS[type].kind === kind;
  });
}

/** Selection first, then the last one used, then the only one there is. */
export function currentLoader(app, kind) {
  return pickTarget(loaderNodesOf(app, kind), selectionOf(app), lastLoaderNode);
}

/** Put a model name in a loader's widget the way picking it from the list does.
 *  False when the node has no such widget or the name is not one of its options. */
export function setLoaderModel(node, name, canvas = node?.graph?.canvas) {
  const type = loaderTypeOf(node);
  const widget = type ? widgetOf(node, LOADERS[type].widget) : null;
  if (!widget) return false;
  const values = widget.options?.values;
  const list = typeof values === "function" ? values() : values;
  if (!Array.isArray(list) || !list.includes(name)) return false;
  widget.value = name;
  try {
    widget.callback?.(name, canvas, node);
  } finally {
    node.setDirtyCanvas?.(true, true);
  }
  return true;
}

export function loadedModel(node) {
  const type = loaderTypeOf(node);
  const widget = type ? widgetOf(node, LOADERS[type].widget) : null;
  return String(widget?.value ?? "");
}

const titleOf = (node) => node?.title || "Prompt + LoRAs";

/** What the target line calls a node: its title, and its id as well when
 *  another of promptNodes has the same title, so the line tells them apart. */
export function targetLabel(node, promptNodes = []) {
  const title = titleOf(node);
  const twin = node && (promptNodes || []).some((n) => n !== node && titleOf(n) === title);
  return twin ? `${title} · #${node.id}` : title;
}
