// Which Prompt + LoRAs node a LoRA goes to, when the request comes from the
// sidebar and there is no node under the cursor. Takes `app` as an argument so
// it imports nothing from ComfyUI.

export const PROMPT_NODE = "Warp Lora Prompt";

const TEXT_WIDGET = "text";

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

export function insertIntoNode(node, stem) {
  if (!node) return false;
  // The prompt owns it: this lands on a line of its own, like typing would.
  // The fallback below is for a browser opened without the prompt UI.
  if (typeof node._warppipeAddLora === "function") {
    const added = node._warppipeAddLora(stem) === true;
    if (added) node.setDirtyCanvas?.(true, true);
    return added;
  }
  const widget = (node.widgets || []).find((w) => w.name === TEXT_WIDGET);
  if (!widget) return false;

  const tag = `<lora:${stem}:1.0>`;
  const el = widget.inputEl;
  const current = widget.value || "";
  if (el && typeof el.selectionStart === "number") {
    const at = el.selectionStart;
    const next = `${current.slice(0, at)}${tag}${current.slice(el.selectionEnd)}`;
    widget.value = next;
    el.value = next;
    const caret = at + tag.length;
    el.setSelectionRange(caret, caret);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  } else {
    widget.value = current ? `${current} ${tag}` : tag;
  }
  node.setDirtyCanvas?.(true, true);
  return true;
}

const titleOf = (node) => node?.title || "Prompt + LoRAs";

/** What the target line calls a node: its title, and its id as well when
 *  another of promptNodes has the same title, so the line tells them apart. */
export function targetLabel(node, promptNodes = []) {
  const title = titleOf(node);
  const twin = node && (promptNodes || []).some((n) => n !== node && titleOf(n) === title);
  return twin ? `${title} · #${node.id}` : title;
}
