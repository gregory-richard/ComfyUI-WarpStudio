// What a prompt list holds, and what one Gallery click does to it.
// Pure string functions: every call reads the text it is given.

const TAG = /<lora:([^:>]+):(-?[0-9]*\.?[0-9]+)([^>]*)>/i;
const SECTION_LINE = /^\s*\/\/\/\//;
const PARKED_LINE = /^\s*\/\//;
const COMMENT_LINE = /^(\s*)\/{2,} ?/;

const same = (a, b) => a.toLowerCase() === b.toLowerCase();

const str = (v) => String(v ?? "");
const eolOf = (text) => (text.includes("\r\n") ? "\r\n" : "\n");

export function loraLines(text) {
  const out = [];
  let start = 0;
  for (const raw of str(text).split("\n")) {
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    const end = start + line.length;
    if (!SECTION_LINE.test(line)) {
      const m = TAG.exec(line);
      if (m) {
        out.push({ name: m[1], state: PARKED_LINE.test(line) ? "parked" : "on", start, end });
      }
    }
    start += raw.length + 1;
  }
  return out;
}

function findLine(text, stem) {
  const lines = loraLines(text).filter((l) => same(l.name, stem));
  return lines.find((l) => l.state === "on") || lines[0] || null;
}

export function loraState(text, stem) {
  const line = findLine(text, str(stem));
  return line ? line.state : null;
}

/* The tag grammar cannot hold a name with ":" or ">" in it, so no line for it could
   be recognised again; such a stem leaves the text alone and has no state. */
const holdable = (stem) => !/[:>]/.test(stem);

/** One click: absent -> on, on -> parked, parked -> on. Returns the new text and
 *  the state it holds, or the same text and a null state for a stem it cannot hold. */
export function cycleLora(text, stem) {
  text = str(text);
  stem = str(stem);
  if (!holdable(stem)) return { text, state: null };
  const line = findLine(text, stem);
  if (!line) {
    const lead = text && !text.endsWith("\n") ? eolOf(text) : "";
    const added = `${text}${lead}<lora:${stem}:1.00>`;
    return { text: added, state: loraState(added, stem) };
  }
  const old = text.slice(line.start, line.end);
  const parked = line.state === "on";
  const next = parked ? old.replace(/^(\s*)/, "$1// ") : old.replace(COMMENT_LINE, "$1");
  const result = text.slice(0, line.start) + next + text.slice(line.end);
  return { text: result, state: loraState(result, stem) };
}

/* Whole-token match of embedding:stem, in a line that is not a comment. */
export function hasEmbedding(text, stem) {
  const want = `embedding:${str(stem)}`.toLowerCase();
  for (const line of str(text).toLowerCase().split("\n")) {
    if (PARKED_LINE.test(line)) continue;
    let from = 0;
    for (;;) {
      const at = line.indexOf(want, from);
      if (at < 0) break;
      const before = at === 0 ? "" : line[at - 1];
      const rest = line.slice(at + want.length);
      if (!/[^\s,([]/.test(before) && !/^[\w-]/.test(rest) && !/^\.\w/.test(rest)) return true;
      from = at + 1;
    }
  }
  return false;
}

export function addEmbedding(text, stem) {
  text = str(text);
  stem = str(stem);
  if (hasEmbedding(text, stem)) return text;
  const body = text.trimEnd();
  const tag = `embedding:${stem}`;
  if (!body.trim()) return tag;
  const last = body.slice(body.lastIndexOf("\n") + 1);
  if (PARKED_LINE.test(last)) return `${body}${eolOf(text)}${tag}`;
  return body.endsWith(",") ? `${body} ${tag}` : `${body}, ${tag}`;
}
