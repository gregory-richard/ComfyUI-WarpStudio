/* The panel remembers its scroll offset per view, so leaving a tab and coming
   back lands where the reader was. */

/* The nearest ancestor that scrolls, or null. `style` reads computed styles. */
export function findScroller(el, style) {
  for (let node = el?.parentElement; node; node = node.parentElement) {
    if (/(auto|scroll)/.test(style(node).overflowY)) return node;
  }
  return null;
}

export function createScrollMemory() {
  const offsets = new Map();
  return {
    save(key, top) {
      if (key != null && Number.isFinite(top)) offsets.set(key, Math.max(0, top));
    },
    get: (key) => offsets.get(key) ?? 0,
  };
}
