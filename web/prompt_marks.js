// What a prompt list holds, and what one Gallery click does to it.
// Pure string functions: every call reads the text it is given.

const TAG = /<lora:([^:>]+):(-?[0-9]*\.?[0-9]+)([^>]*)>/i;
const SECTION_LINE = /^\s*\/\/\/\//;
const PARKED_LINE = /^\s*\/\//;
const COMMENT_LINE = /^(\s*)\/\/ ?/;

const same = (a, b) => a.toLowerCase() === b.toLowerCase();

export function loraLines(text) {
  const out = [];
  let start = 0;
  for (const line of String(text).split("\n")) {
    const end = start + line.length;
    if (!SECTION_LINE.test(line)) {
      const m = TAG.exec(line);
      if (m) {
        out.push({ name: m[1], state: PARKED_LINE.test(line) ? "parked" : "on", start, end });
      }
    }
    start = end + 1;
  }
  return out;
}

function findLine(text, stem) {
  const lines = loraLines(text).filter((l) => same(l.name, stem));
  return lines.find((l) => l.state === "on") || lines[0] || null;
}

export function loraState(text, stem) {
  const line = findLine(text, stem);
  return line ? line.state : null;
}

export function cycleLora(text, stem) {
  const line = findLine(text, stem);
  if (!line) {
    const lead = text && !text.endsWith("\n") ? "\n" : "";
    return { text: `${text}${lead}<lora:${stem}:1.00>`, state: "on" };
  }
  const old = text.slice(line.start, line.end);
  const parked = line.state === "on";
  const next = parked ? old.replace(/^(\s*)/, "$1// ") : old.replace(COMMENT_LINE, "$1");
  return {
    text: text.slice(0, line.start) + next + text.slice(line.end),
    state: parked ? "parked" : "on",
  };
}

export function hasEmbedding(text, stem) {
  const want = `embedding:${stem}`.toLowerCase();
  const hay = text.toLowerCase();
  let from = 0;
  for (;;) {
    const at = hay.indexOf(want, from);
    if (at < 0) return false;
    const before = at === 0 ? "" : hay[at - 1];
    const after = hay[at + want.length] ?? "";
    if (!/[^\s,]/.test(before) && !/[\w.-]/.test(after)) return true;
    from = at + 1;
  }
}

export function addEmbedding(text, stem) {
  if (hasEmbedding(text, stem)) return text;
  if (!text.trim()) return `embedding:${stem}`;
  return `${text.trimEnd()}, embedding:${stem}`;
}
