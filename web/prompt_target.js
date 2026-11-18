// Which Prompt + LoRAs node a LoRA goes to, when the request comes from the
// sidebar and there is no node under the cursor. Takes `app` as an argument so
// it imports nothing from ComfyUI.

export const PROMPT_NODE = "Warp Lora Prompt";

const TEXT_WIDGET = "text";

let lastUsed = null;

export function noteUsed(node) {
  if (node && node.id != null) lastUsed = node.id;
}

export function lastUsedId() {
  return lastUsed;
}

/** Selection first, then the last one used, then the only one there is. */
export function pickTarget(promptNodes, selectedIds, lastUsedNodeId) {
  const nodes = promptNodes || [];
  const selected = new Set(selectedIds || []);
  const picked = nodes.find((n) => selected.has(n.id));
  if (picked) return picked;
  if (lastUsedNodeId != null) {
    const again = nodes.find((n) => n.id === lastUsedNodeId);
    if (again) return again;
  }
  return nodes.length === 1 ? nodes[0] : null;
}

const isPrompt = (n) => n && (n.type === PROMPT_NODE || n.comfyClass === PROMPT_NODE);

export function currentTarget(app) {
  const graph = app?.graph;
  const all = graph?._nodes ?? graph?.nodes ?? [];
  const nodes = Array.from(all).filter(isPrompt);
  const selection = Object.values(app?.canvas?.selected_nodes || {}).map((n) => n?.id);
  return pickTarget(nodes, selection, lastUsedId());
}

export function insertIntoNode(node, stem) {
  if (!node) return false;
  // The prompt owns it: this lands on a line of its own, like typing would.
  // The fallback below is for a browser opened without the prompt UI.
  if (typeof node._warppipeAddLora === "function") {
    node._warppipeAddLora(stem);
    node.setDirtyCanvas?.(true, true);
    return true;
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

export function targetLabel(node) {
  return node?.title || "Prompt + LoRAs";
}
