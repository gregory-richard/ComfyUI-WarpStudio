// The Prompt Writer's chat history: a store of chats, each a list of prompt
// versions. Pure data handling, so it can be tested under node.

import { diffStats, diffWords } from "./prompt_writer_core.js";

export const CHATS_FILE = "warppipe_prompt_chats.json";
/** Written by the LLM. */
export const KIND_WRITTEN = "written";
/** Edited by hand, in the Prompt Writer or in the node's prompt box. */
export const KIND_HAND = "hand";
/** Taken from a Prompt + LoRAs, and not close to any version. */
export const KIND_NODE = "node";
const KINDS = [KIND_WRITTEN, KIND_HAND, KIND_NODE];
/** Start from the text in the prompt box of the For node, read when Write is pressed. */
export const CURRENT_PROMPT = "prompt";
/** Start from nothing: a new prompt from the request alone. */
export const NEW_PROMPT = "none";

const STORE_VERSION = 1;
const DEFAULT_TITLE = "New chat";
const TITLE_WORDS = 6;
const TITLE_CHARS = 40;

export function emptyStore() {
  return { version: STORE_VERSION, chats: [] };
}

const isObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const isId = (v) => typeof v === "string" && v !== "";
const stringOr = (v, fallback) => (typeof v === "string" ? v : fallback);
const numberOrNull = (v) => (Number.isFinite(v) ? v : null);
const stampOf = (v) => (Number.isFinite(v) || isId(v) ? v : 0);

function normalizeImages(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.filter((image) => isId(image) || isObject(image));
}

function isSuggestion(raw) {
  return isObject(raw) && isId(raw.id) && isId(raw.tag) && Number.isFinite(raw.strength);
}

/** Only what names the LoRA and its weight is required: the rest is for display. */
function normalizeSuggestions(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isSuggestion).map((item) => ({
    id: item.id,
    name: stringOr(item.name, ""),
    title: stringOr(item.title, ""),
    tag: item.tag,
    thumbnail: stringOr(item.thumbnail, null),
    triggers: Array.isArray(item.triggers)
      ? item.triggers.filter((word) => typeof word === "string")
      : [],
    strength: item.strength,
    reason: stringOr(item.reason, ""),
    ...(item.addedHow === "appended" || item.addedHow === "enabled"
      ? { addedHow: item.addedHow }
      : {}),
  }));
}

const isTurn = (raw) => isObject(raw) && isId(raw.question) && isId(raw.answer);

/** The questions the AI asked on the way to a version, each with its answer. */
function normalizeTurns(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.filter(isTurn).map(({ question, answer }) => ({ question, answer }));
}

const isPending = (raw) => isObject(raw) && isId(raw.question) && typeof raw.request === "string";
const isFailure = (raw) => isObject(raw) && isId(raw.message) && typeof raw.request === "string";

/** A step that waits for the answer to a question, or null. */
function normalizePending(raw, baseId = raw?.baseId) {
  if (!isPending(raw)) return null;
  return {
    request: raw.request,
    images: normalizeImages(raw.images),
    baseId: isId(baseId) ? baseId : null,
    turns: normalizeTurns(raw.turns),
    question: raw.question,
  };
}

/** A request that failed: what a Retry needs, and why it failed. Nothing that lives only in the page. */
function normalizeFailure(raw, baseId = raw?.baseId) {
  if (!isFailure(raw)) return null;
  return {
    request: raw.request,
    images: normalizeImages(raw.images),
    baseId: isId(baseId) ? baseId : null,
    turns: normalizeTurns(raw.turns),
    answer: stringOr(raw.answer, null),
    message: raw.message,
  };
}

function normalizeVersion(raw, n) {
  return {
    id: raw.id,
    n,
    created: stampOf(raw.created),
    kind: KINDS.includes(raw.kind) ? raw.kind : KIND_WRITTEN,
    request: stringOr(raw.request, ""),
    images: normalizeImages(raw.images),
    baseId: isId(raw.baseId) ? raw.baseId : null,
    text: raw.text,
    model: stringOr(raw.model, ""),
    systemPrompt: stringOr(raw.systemPrompt, ""),
    cost: numberOrNull(raw.cost),
    seconds: numberOrNull(raw.seconds),
    usage: isObject(raw.usage) ? { ...raw.usage } : {},
    suggestions: normalizeSuggestions(raw.suggestions),
    activeLoras: normalizeSuggestions(raw.activeLoras),
    questions: normalizeTurns(raw.questions),
    reasoning: stringOr(raw.reasoning, ""),
    editedIn: raw.editedIn === "node" ? "node" : null,
    source: stringOr(raw.source, null),
    sentAt: numberOrNull(raw.sentAt),
  };
}

// The first entry of each id, among those `valid` accepts.
function validUnique(items, valid) {
  const seen = new Set();
  return items.filter((item) => {
    if (!isObject(item) || !isId(item.id) || !valid(item) || seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

/**
 * Chats before 5.1 kept the node prompt an edit started from only as a quote
 * (`basePrompt`, or `baseId: "seed"` with the seed prompt). The quote becomes
 * the version it is (or is close to) or a new one, inserted before the step.
 * Any other base that names no earlier version becomes a root.
 */
function normalizeChat(raw) {
  const seedPrompt = stringOr(raw.seedPrompt, null);
  const targetLabel = stringOr(raw.targetLabel, null);
  const versions = [];
  const baseOf = (step, id, created) => {
    const quoted =
      typeof step.basePrompt === "string"
        ? step.basePrompt
        : step.baseId === "seed"
          ? seedPrompt
          : null;
    if (quoted?.trim())
      return adoptInto(versions, quoted, { id, now: created, source: targetLabel }).id;
    return versions.some((v) => v.id === step.baseId) ? step.baseId : null;
  };
  for (const v of validUnique(raw.versions, (v) => typeof v.text === "string")) {
    const baseId = baseOf(v, `${v.id}~node`, stampOf(v.created));
    versions.push(normalizeVersion({ ...v, baseId }, versions.length + 1));
  }
  const updated = stampOf(raw.updated);
  const pending = isPending(raw.pending)
    ? normalizePending(raw.pending, baseOf(raw.pending, `${raw.id}~pending`, updated))
    : null;
  const failure = isFailure(raw.failure)
    ? normalizeFailure(raw.failure, baseOf(raw.failure, `${raw.id}~failure`, updated))
    : null;
  const known = new Set(versions.map((v) => v.id));
  return {
    id: raw.id,
    title: stringOr(raw.title, DEFAULT_TITLE),
    titleEdited: raw.titleEdited === true,
    created: stampOf(raw.created),
    updated,
    seedPrompt,
    targetLabel,
    versions,
    pending,
    failure,
    folded: Array.isArray(raw.folded) ? [...new Set(raw.folded.filter((id) => known.has(id)))] : [],
  };
}

/** Is this a chat file this version can read? One it cannot must not be saved over. */
export function isStore(raw) {
  return isObject(raw) && raw.version === STORE_VERSION && Array.isArray(raw.chats);
}

/** A valid store from anything: bad input is empty, malformed entries are dropped. */
export function normalizeStore(raw) {
  if (!isStore(raw)) return emptyStore();
  const chats = validUnique(raw.chats, (c) => Array.isArray(c.versions));
  return { version: STORE_VERSION, chats: chats.map(normalizeChat) };
}

export function createChat(store, { id, now, seedPrompt = null, targetLabel = null }) {
  const chat = {
    id,
    title: DEFAULT_TITLE,
    titleEdited: false,
    created: now,
    updated: now,
    seedPrompt,
    targetLabel,
    versions: [],
    pending: null,
    failure: null,
    folded: [],
  };
  store.chats.push(chat);
  return chat;
}

/** The first words of a text, or "" when it has none. */
function firstWords(text) {
  const words = String(text ?? "")
    .trim()
    .split(/\s+/)
    .slice(0, TITLE_WORDS)
    .join(" ");
  // The cut may fall in the middle of an emoji's surrogate pair.
  return words
    .slice(0, TITLE_CHARS)
    .replace(/[\uD800-\uDBFF]$/, "")
    .trimEnd();
}

/** The first words of a request, as a chat title. */
export function titleFrom(request) {
  return firstWords(request) || DEFAULT_TITLE;
}

function makeVersion(
  n,
  {
    id,
    now,
    kind = KIND_WRITTEN,
    request = "",
    images = [],
    baseId = null,
    text,
    model = "",
    systemPrompt = "",
    cost = null,
    seconds = null,
    usage = {},
    suggestions = [],
    activeLoras = [],
    questions = [],
    reasoning = "",
    editedIn = null,
    source = null,
  }
) {
  return {
    id,
    n,
    created: now,
    kind,
    request,
    images: [...images],
    baseId,
    text,
    model,
    systemPrompt,
    cost,
    seconds,
    usage: { ...usage },
    suggestions: normalizeSuggestions(suggestions),
    activeLoras: normalizeSuggestions(activeLoras),
    questions: normalizeTurns(questions),
    reasoning: stringOr(reasoning, ""),
    editedIn,
    source,
    sentAt: null,
  };
}

/** A new version at the end. The first one the LLM writes names the chat. */
export function addVersion(chat, fields) {
  const version = makeVersion(chat.versions.length + 1, fields);
  const named = chat.versions.some((v) => v.kind === KIND_WRITTEN);
  chat.versions.push(version);
  chat.updated = fields.now;
  if (!chat.titleEdited && !named && version.kind === KIND_WRITTEN) {
    chat.title = titleFrom(version.request);
  }
  return version;
}

/** A hand edit of a version; `editedIn: "node"` when it was made in the node's prompt box. */
export function addHandVersion(chat, { id, now, baseId, text, editedIn = null }) {
  return addVersion(chat, { id, now, kind: KIND_HAND, baseId, text, editedIn });
}

/** A prompt taken from a node that is not close to any version: a start of its own. */
export function addNodeVersion(chat, { id, now, text, source = null }) {
  return addVersion(chat, { id, now, kind: KIND_NODE, text, source });
}

/** Send to prompt wrote this version into a node. */
export function markSent(version, now) {
  version.sentAt = now;
}

const WORDS = /[\p{L}\p{N}]+/gu;

function wordCounts(text) {
  const counts = new Map();
  for (const word of String(text).toLowerCase().match(WORDS) ?? []) {
    counts.set(word, (counts.get(word) ?? 0) + 1);
  }
  return counts;
}

/** The share of two texts' words they have in common, 0 to 1. */
function sharedWords(a, b) {
  let shared = 0;
  let total = 0;
  for (const [word, count] of a) {
    shared += Math.min(count, b.get(word) ?? 0);
    total += count;
  }
  for (const count of b.values()) total += count;
  return total ? (2 * shared) / total : 0;
}

/**
 * What a prompt text is to these versions: the newest one that holds exactly
 * it ("same"), the one it is a tweak of ("close": not a rewrite, the test that
 * shows "Mostly rewritten"), or null. Only the best candidate by shared words
 * is diffed.
 */
export function matchText(versions, text) {
  for (let i = versions.length - 1; i >= 0; i--) {
    if (versions[i].text === text) return { how: "same", version: versions[i] };
  }
  const words = wordCounts(text);
  let best = null;
  let bestScore = 0;
  for (const version of versions) {
    const score = sharedWords(wordCounts(version.text), words);
    if (score > 0 && score >= bestScore) {
      best = version;
      bestScore = score;
    }
  }
  if (!best || diffStats(diffWords(best.text, text)).rewrite) return null;
  return { how: "close", version: best };
}

function adoptInto(versions, text, { id, now, source = null }) {
  const match = matchText(versions, text);
  if (match?.how === "same") return match.version;
  const fields = match
    ? { id, now, kind: KIND_HAND, baseId: match.version.id, text, editedIn: "node" }
    : { id, now, kind: KIND_NODE, text, source };
  const version = makeVersion(versions.length + 1, fields);
  versions.push(version);
  return version;
}

/**
 * The version a node's prompt text is in this chat: the one that holds it, a
 * new hand edit (in the node) of the one it is close to, or a new node version.
 */
export function adoptText(chat, text, { id, now, source = null }) {
  const count = chat.versions.length;
  const version = adoptInto(chat.versions, text, { id, now, source });
  if (chat.versions.length > count) chat.updated = now;
  return version;
}

const sameWords = (a, b) => a.replace(/\s+/g, " ").trim() === b.replace(/\s+/g, " ").trim();

/**
 * Has something started from this version, or gone out of the Prompt Writer
 * with it? `writingBaseId` is the base of a write in progress.
 */
export function isUsed(chat, version, writingBaseId = null) {
  const id = version.id;
  return (
    version.sentAt != null ||
    writingBaseId === id ||
    chat.pending?.baseId === id ||
    chat.failure?.baseId === id ||
    chat.versions.some((v) => v.baseId === id)
  );
}

/**
 * Typed text saved to a version: a hand edit nothing has used yet takes it in
 * place; anything else stays as it was and gets a hand edit under it. Null
 * when the text is empty or only its spacing changed: nothing is saved.
 */
export function editVersion(chat, version, text, { id, now, writingBaseId = null }) {
  if (!text.trim()) return null;
  if (version.kind === KIND_HAND && !isUsed(chat, version, writingBaseId)) {
    version.text = text;
    chat.updated = now;
    return { version, made: false };
  }
  if (sameWords(text, version.text)) return null;
  return { version: addHandVersion(chat, { id, now, baseId: version.id, text }), made: true };
}

/**
 * The text a new version builds on: "seed" is the seed prompt, a version id
 * that version, and null the newest version, else the seed prompt.
 */
export function baseText(chat, baseId) {
  if (baseId === "seed") return chat.seedPrompt ?? null;
  if (baseId != null) return chat.versions.find((v) => v.id === baseId)?.text ?? null;
  return chat.versions.at(-1)?.text ?? chat.seedPrompt ?? null;
}

/**
 * The AI asked instead of writing: the step waits in the chat for an answer.
 * `step` is {request, images, baseId, turns}, the turns being the questions
 * already answered on the way.
 */
export function openQuestion(chat, step, question, now) {
  chat.pending = {
    request: step.request ?? "",
    images: [...(step.images ?? [])],
    baseId: step.baseId ?? null,
    turns: normalizeTurns(step.turns),
    question,
  };
  chat.updated = now;
  return chat.pending;
}

/** Keep a failed request with the chat, so a reload still shows it with its Retry. */
export function recordFailure(chat, spec, message, now) {
  chat.failure = normalizeFailure({
    request: spec.request ?? "",
    images: spec.images,
    baseId: spec.baseId,
    turns: spec.turns,
    answer: spec.answer,
    message,
  });
  chat.updated = now;
  return chat.failure;
}

/** The failed request leaves the chat; says whether there was one. */
export function clearFailure(chat) {
  const had = !!chat.failure;
  chat.failure = null;
  return had;
}

/** The waiting step leaves the chat; returned, or null when there was none. */
export function dropQuestion(chat) {
  const pending = chat.pending ?? null;
  chat.pending = null;
  return pending;
}

/** A version in a word or a few: what was asked for, or how it came to be. */
function gist(version) {
  if (version.kind === KIND_HAND) {
    return version.editedIn === "node" ? "edited in the node" : "edited by hand";
  }
  if (version.kind === KIND_NODE) return "taken from the node";
  return firstWords(version.request) || "optimised";
}

/** What the next Write does: edit a version, newest first, or the current prompt, or write anew. */
export function startOptions(chat, targetLabel) {
  const versions = [...chat.versions].reverse().map((v, i) => ({
    value: v.id,
    label: `Edit v${v.n} (${i === 0 ? "newest" : gist(v)})`,
  }));
  return [
    ...versions,
    { value: CURRENT_PROMPT, label: `Edit current prompt (${targetLabel || "the For node"})` },
    { value: NEW_PROMPT, label: "New prompt" },
  ];
}

/** Which of them is chosen: the draft's own choice while it exists, else the newest version. */
export function startValue(chat, base) {
  if (base === CURRENT_PROMPT || base === NEW_PROMPT) return base;
  if (base != null && chat.versions.some((v) => v.id === base)) return base;
  return chat.versions.at(-1)?.id ?? NEW_PROMPT;
}

/** How a step that is not a version yet says where it started. */
export function startLabel(chat, baseId) {
  const version = chat.versions.find((v) => v.id === baseId);
  return version ? `edit of v${version.n}` : "new prompt";
}

/** How a version says where it came from. */
export function originLabel(chat, version) {
  if (version.kind === KIND_NODE) return `from ${version.source || "the node"}`;
  const base = chat.versions.find((v) => v.id === version.baseId);
  if (version.kind === KIND_HAND) {
    if (!base) return "edited by hand";
    return `hand edit of v${base.n}${version.editedIn === "node" ? ", in the node" : ""}`;
  }
  return base ? `edit of v${base.n}` : "new prompt";
}

/** A short line for a closed row: the request, or how a version came to be. */
export function rowText(version) {
  if (version.kind === KIND_WRITTEN) return version.request || "Optimise the prompt";
  const said = gist(version);
  return said[0].toUpperCase() + said.slice(1);
}

// --- The history as a graph, and folding -----------------------------------------

function childrenMap(chat) {
  const kids = new Map(chat.versions.map((v) => [v.id, []]));
  for (const v of chat.versions) kids.get(v.baseId)?.push(v);
  return kids;
}

/**
 * Would folding this version hide anything? Versions in `keep` never fold away,
 * so a fold hides only the follow-ups that lead elsewhere.
 */
export function canFold(chat, id, keep = new Set()) {
  return chat.versions.some((v) => v.baseId === id && !keep.has(v.id));
}

/** How many versions the fold of this one hides now. */
export function foldedCount(chat, id, keep = new Set()) {
  if (!chat.folded.includes(id)) return 0;
  const shown = new Set(visibleVersions(chat, keep).map((v) => v.id));
  const kids = childrenMap(chat);
  const count = (key) =>
    (kids.get(key) ?? []).reduce((sum, v) => sum + (shown.has(v.id) ? 0 : 1) + count(v.id), 0);
  return count(id);
}

export function toggleFold(chat, id) {
  chat.folded = chat.folded.includes(id)
    ? chat.folded.filter((f) => f !== id)
    : [...chat.folded, id];
}

/** The version and those it came from, back to its start. */
export function pathTo(chat, id) {
  const byId = new Map(chat.versions.map((v) => [v.id, v]));
  const path = new Set();
  for (let v = byId.get(id); v && !path.has(v.id); v = byId.get(v.baseId)) path.add(v.id);
  return path;
}

/** Show the version: nothing it came from stays folded. */
export function unfoldTo(chat, id) {
  const path = pathTo(chat, id);
  path.delete(id);
  chat.folded = chat.folded.filter((f) => !path.has(f));
}

/** These versions and everything they came from: what folding never hides. */
export function keptPaths(chat, ids) {
  const keep = new Set();
  for (const id of ids) for (const kept of pathTo(chat, id)) keep.add(kept);
  return keep;
}

/**
 * The versions in order, without those under a folded one. `keep` (closed
 * under what each came from, see keptPaths) always shows.
 */
export function visibleVersions(chat, keep = new Set()) {
  const folded = new Set(chat.folded);
  const hidden = new Set();
  for (const v of chat.versions) {
    if (keep.has(v.id)) continue;
    if (folded.has(v.baseId) || hidden.has(v.baseId)) hidden.add(v.id);
  }
  return chat.versions.filter((v) => !hidden.has(v.id));
}

/**
 * Lanes for a git-like graph of `items` ({id, baseId}, in order, a base before
 * what was made from it). A first follow-up continues its base's lane; any
 * other starts a chain of its own, from its base's row. Chains take the lowest
 * lane free over their rows, so as few lanes as possible are used.
 */
export function historyLayout(items) {
  const rowOf = new Map(items.map((item, i) => [item.id, i]));
  const firstFollowUp = new Map();
  for (const item of items) {
    if (rowOf.has(item.baseId) && !firstFollowUp.has(item.baseId)) {
      firstFollowUp.set(item.baseId, item.id);
    }
  }
  const chains = [];
  const chainOf = new Map();
  items.forEach((item, row) => {
    const based = rowOf.has(item.baseId);
    if (based && firstFollowUp.get(item.baseId) === item.id) {
      const chain = chainOf.get(item.baseId);
      chain.end = row;
      chainOf.set(item.id, chain);
      return;
    }
    const chain = { start: based ? rowOf.get(item.baseId) : row, end: row, lane: 0 };
    chains.push(chain);
    chainOf.set(item.id, chain);
  });
  const laneEnds = [];
  for (const chain of [...chains].sort((a, b) => a.start - b.start)) {
    let lane = laneEnds.findIndex((end) => end < chain.start);
    if (lane < 0) lane = laneEnds.length;
    chain.lane = lane;
    laneEnds[lane] = chain.end;
  }
  return {
    lanes: laneEnds.length,
    rows: items.map((item) => ({
      id: item.id,
      lane: chainOf.get(item.id).lane,
      parentRow: rowOf.has(item.baseId) ? rowOf.get(item.baseId) : null,
    })),
  };
}

/**
 * A request put back into a composer that may already hold something: the
 * request comes first and nothing typed is lost. `overflow` says images were
 * left out because more than `maxImages` would not be sent.
 */
export function mergedDraft(held, back, maxImages) {
  const images = [...new Set([...back.images, ...held.images])];
  return {
    text: [back.text, held.text].filter((part) => part.trim()).join("\n\n"),
    images: images.slice(0, maxImages),
    overflow: images.length > maxImages,
  };
}

/** A generated title; one the user typed always stays. */
export function setAutoTitle(chat, title) {
  const trimmed = String(title ?? "").trim();
  if (!trimmed || chat.titleEdited) return false;
  chat.title = trimmed;
  return true;
}

export function renameChat(chat, title) {
  const trimmed = String(title ?? "").trim();
  if (!trimmed) return;
  chat.title = trimmed;
  chat.titleEdited = true;
}

export function deleteChat(store, id) {
  const index = store.chats.findIndex((c) => c.id === id);
  if (index < 0) return false;
  store.chats.splice(index, 1);
  return true;
}

// Stamps are whatever the caller passed as `now`: epoch numbers or ISO strings.
function timeOf(stamp) {
  return typeof stamp === "number" ? stamp : Date.parse(stamp) || 0;
}

/** A new array, newest updated first. */
export function sortedChats(store) {
  return [...store.chats].sort((a, b) => timeOf(b.updated) - timeOf(a.updated));
}

/** The most recently updated chat for this target, or null. */
export function latestChatFor(store, label) {
  if (label == null) return null;
  return sortedChats(store).find((chat) => chat.targetLabel === label) ?? null;
}
