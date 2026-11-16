/* The Rename view of the Civitai Updater panel.

   ComfyUI loads every .js file in this folder as an extension module, so this
   one must do nothing on import: it only exports functions. civitai_updater.js
   imports it, passes its request helpers in and calls renderRenameView. */

const ROUTE = "/civitai-updater";
const DRY_RUN_JOB = "rename-dry-run";
const APPLY_JOB = "rename-apply";
const DOWNLOAD_JOB = "naming-model-download";
export const RENAME_JOB_TYPES = [DRY_RUN_JOB, APPLY_JOB, DOWNLOAD_JOB];
const NO_SEND2TRASH = "Removing extra copies needs the send2trash package. Install it into ComfyUI's Python (pip install send2trash) and restart ComfyUI.";
const PAGE = 150;
const LAST_JOB_KEY = "civitai-updater.rename.dryRunJob";
const SELECTION_KEY = "civitai-updater.rename.selection";
const NAMED = new Set(["change", "unchanged"]);
const PART_WORDS = { creator: "creator", title: "title", version: "version", precision: "precision", base: "base code" };

/* Flags that make the new name unsafe or impossible: such a row cannot be
   selected for Apply until it is edited or kept. */
const BLOCKING_KINDS = new Set(["empty", "forbidden", "separator", "reserved", "trailing", "collision", "exists"]);

const KEPT_REASONS = {
  "not-on-civitai": "Not on Civitai",
  "release-gone": "Release gone from Civitai",
  "not-main-file": "Not the main model file",
  "type-mismatch": "Type doesn't fit the folder",
  "kept-by-you": "Current name kept by you",
  "dry-run-again": "Run a new dry run to name it again",
};

/* ---------------------------------------------------------------- pure helpers */

export function createRenameStore() {
  return {
    status: null,
    statusError: "",
    jobId: null,
    rows: [],
    byKey: new Map(),
    runStatus: "",
    runActive: false,
    runLine: "",
    summary: null,
    errors: [],
    applied: false,
    restoredFrom: null,
    forgottenJobId: null,
    persistWarning: "",
    loadedJobId: null,
    selected: new Map(),
    removeDup: new Set(),
    open: { unchanged: false, kept: false },
    baseOpen: false,
    baseCodesEmptyBeforeRun: null,
    limits: {},
    drafts: new Map(),
    baseDrafts: new Map(),
    editing: null,
    confirming: false,
    busy: false,
    message: null,
    result: null,
    recoverResult: null,
    revealError: null,
    restoredLastJob: false,
  };
}

function flagParts(flag) {
  const text = String(flag || "");
  const i = text.indexOf(":");
  return i < 0 ? [text, ""] : [text.slice(0, i), text.slice(i + 1)];
}

export function isBlockingFlag(flag) {
  return BLOCKING_KINDS.has(flagParts(flag)[0]);
}

const lower = (text) => String(text ?? "").toLowerCase();
const quoted = (items) => items.map((item) => `"${item}"`).join(", ");

/* The other named rows a clash flag is about, found in the dry run's rows. */
function otherRows(row, rows, match) {
  if (!row || !Array.isArray(rows)) return [];
  return rows.filter((other) => other && other !== row && other.key !== row.key && NAMED.has(other.status) && match(other));
}

function collisionPeers(row, rows) {
  return otherRows(row, rows, (other) => lower(other.folder) === lower(row.folder) && lower(other.newStem) === lower(row.newStem));
}

function titleClashTitles(row, rows) {
  const parts = row?.parts || {};
  const peers = otherRows(row, rows, (other) => String(other.modelId) !== String(row.modelId)
    && ["creator", "title", "base"].every((name) => (other.parts?.[name] ?? "") === (parts[name] ?? "")));
  return [...new Set(peers.map((other) => String(other.liveTitle || other.oldStem || "")).filter(Boolean))];
}

function versionClashPeers(row, rows) {
  const parts = row?.parts || {};
  return otherRows(row, rows, (other) => String(other.modelId) === String(row.modelId)
    && String(other.versionId) !== String(row.versionId)
    && ["version", "precision", "base"].every((name) => (other.parts?.[name] ?? "") === (parts[name] ?? "")));
}

/* One flag code in plain words: {text, level, action?}. level is "block"
   (the name cannot be applied as it is), "warn" (check it) or "info".
   rows (the whole dry run, optional) lets a clash name the other file or model. */
export function flagInfo(flag, row, rows) {
  const [kind, arg] = flagParts(flag);
  const parts = (row && row.parts) || {};
  const uploader = String(row?.uploader || "");
  const value = arg ? String(parts[arg] ?? "") : "";
  const partWord = PART_WORDS[arg] || arg;
  switch (kind) {
    case "invented":
      if (/^v\d/i.test(arg)) return { level: "warn", text: `"${arg}" is not in Civitai's version name` };
      return { level: "warn", text: `"${arg}" is not in Civitai's title, version names or file names` };
    case "unparsed":
      return { level: "warn", text: "The naming model's answer could not be read, so this name is a fallback. Edit it or ask again." };
    case "empty":
      if (arg === "title" && row?.liveTitle) {
        // Civitai's title is the card's header.
        return { level: "block", text: "The AI gave no title. Type one, or press Ask again." };
      }
      return { level: "block", text: `The ${partWord} is empty` };
    case "separator":
      if (arg) return { level: "block", text: `The ${partWord} "${value}" contains " - "` };
      return { level: "block", text: 'A part starts or ends with "-", which adds an extra " - " to the name' };
    case "forbidden":
      return { level: "block", text: `The ${partWord} "${value}" has a character Windows does not allow in file names` };
    case "reserved":
      return { level: "block", text: `"${row?.newStem || ""}" is a name Windows reserves` };
    case "trailing":
      return { level: "block", text: "The new name ends with a dot or a space" };
    case "non-ascii":
      return { level: "warn", text: "The new name has emojis or non-English letters; plain letters, digits, spaces, dots and hyphens are safest" };
    case "base-word":
      return { level: "warn", text: `“${arg}” is a base-model name — usually it doesn't belong here` };
    case "version-format":
      return { level: "warn", text: `Write ${arg.replace(/(\.0+)+$/, "")} instead of ${arg}` };
    case "creator-from-file-name":
      return { level: "warn", text: "Civitai has no creator for this model; taken from your file name" };
    case "maker-from-description": {
      const maker = parts.creator || "";
      // The card's header already says who uploaded it.
      if (!uploader) return { level: "info", text: `The maker "${maker}" comes from the model's description, not the uploader`, action: "use-maker" };
      return { level: "info", text: `The description names ${maker} as the model's maker, so the name starts with ${maker}.`, action: "use-maker" };
    }
    case "maker-unknown":
      return {
        level: "warn",
        text: `This looks like an official model uploaded by ${uploader || "someone else"}, but the description doesn't say who made it. Set the creator yourself.`,
      };
    case "maker-not-in-description":
      return { level: "warn", text: `"${parts.creator || ""}" is not named in the description; check who made it` };
    case "new-base-code":
      return { level: "info", text: `New base code "${parts.base || ""}"${row?.baseModel ? ` for ${row.baseModel}` : ""}`, action: "base-link" };
    case "official-file":
      return { level: "warn", text: "Official file: ComfyUI templates may look for the original name" };
    case "file-not-in-release":
      return { level: "warn", text: "This file is not in the Civitai release (a conversion or a re-upload?)" };
    case "collision": {
      const peers = collisionPeers(row, rows);
      if (!peers.length) return { level: "block", text: "Another file in this folder would get the same name" };
      const names = quoted(peers.map((other) => other.oldStem));
      return {
        level: "block",
        text: peers.length === 1
          ? `Another file in this folder, ${names}, would get the same new name.`
          : `Other files in this folder, ${names}, would get the same new name.`,
      };
    }
    case "title-clash": {
      const titles = titleClashTitles(row, rows);
      if (!titles.length) return { level: "warn", text: "Another model by this creator gets the same title." };
      return {
        level: "warn",
        text: titles.length === 1
          ? `Another model by this creator, ${quoted(titles)}, gets the same title.`
          : `Other models by this creator, ${quoted(titles)}, get the same title.`,
      };
    }
    case "version-clash": {
      const peers = versionClashPeers(row, rows);
      const names = quoted([...new Set(peers.map((other) => other.oldStem))]);
      return {
        level: "warn",
        text: peers.length
          ? `Another version of this model, ${names}, gets the same version and base code; edit one of the versions.`
          : "Another version of this model gets the same version and base code; edit one of the versions.",
      };
    }
    case "exists":
      return { level: "block", text: "A file with the new name is already in the folder" };
    case "ask-again":
      return { level: "info", text: "The naming model names this model again on the next dry run" };
    default:
      return { level: "warn", text: String(flag || "") };
  }
}

export function flagMessage(flag, row, rows) {
  return flagInfo(flag, row, rows).text;
}

/* Why a kept, duplicate or error row is not renamed, in words. A duplicate's
   text is only shown when its kept copy is not on the same card. */
export function reasonText(row, byKey) {
  if (!row) return "";
  if (row.status === "duplicate") {
    const survivor = byKey && row.survivorKey ? byKey.get(row.survivorKey) : null;
    const name = survivor ? survivor.oldStem : fileName(row.survivorPath || row.survivorKey || "");
    return name ? `Extra copy of "${name}"` : "Extra copy of another file";
  }
  if (row.status === "error") return row.reason ? String(row.reason) : "The lookup failed.";
  return KEPT_REASONS[row.reason] || String(row.reason || "Kept");
}

export function sectionOf(row) {
  const status = row?.status;
  if (status === "error" || status === "duplicate") return "look";
  if (status === "kept") return "kept";
  if ((row.flags || []).length) return "look";
  if (status === "change") return "change";
  return "unchanged";
}

const SECTION_ORDER = ["look", "change", "unchanged", "kept"];
const lookRank = (row) => (row.status === "error" ? 0 : row.status === "duplicate" ? 1 : 2);

/* Rows of one model in card order: each extra copy right under the copy it
   duplicates (when that copy is on the card), everything else as listed. */
function cardOrder(rows) {
  const keys = new Set(rows.map((row) => row.key));
  const extras = new Map();
  const top = [];
  for (const row of rows) {
    const survivor = row.status === "duplicate" ? row.survivorKey : null;
    if (survivor && survivor !== row.key && keys.has(survivor)) {
      if (!extras.has(survivor)) extras.set(survivor, []);
      extras.get(survivor).push(row);
    } else {
      top.push(row);
    }
  }
  const out = [];
  const placed = new Set();
  const place = (row) => {
    if (placed.has(row.key)) return;
    placed.add(row.key);
    out.push(row);
    for (const extra of extras.get(row.key) || []) place(extra);
  };
  top.forEach(place);
  for (const row of rows) place(row); // copies that point at each other: never lost
  return out;
}

/* One card per model, in the order the models first appear; a row without a
   model id gets a card of its own. {id, rows, section, rank} */
export function modelCards(rows) {
  const cards = new Map();
  for (const row of rows || []) {
    if (!row) continue;
    const model = row.modelId == null ? "" : String(row.modelId);
    const id = model ? `m:${model}` : `r:${row.key}`;
    if (!cards.has(id)) cards.set(id, { id, rows: [] });
    cards.get(id).rows.push(row);
  }
  return [...cards.values()].map((card) => {
    const ordered = cardOrder(card.rows);
    // A card goes in the most urgent section any of its rows is in.
    const section = SECTION_ORDER[Math.min(...ordered.map((row) => SECTION_ORDER.indexOf(sectionOf(row))))];
    return { id: card.id, rows: ordered, section, rank: Math.min(...ordered.map(lookRank)) };
  });
}

export function groupCards(rows) {
  const sections = { look: [], change: [], unchanged: [], kept: [] };
  for (const card of modelCards(rows)) sections[card.section].push(card);
  // Errors and duplicates first: they need a decision before anything else.
  sections.look.sort((a, b) => a.rank - b.rank);
  return sections;
}

/* What a card's header and thumbnail show: the Civitai title (else the title
   parsed from the current name), the uploader, the type and the first preview. */
export function cardInfo(card) {
  const rows = card?.rows || [];
  const first = rows[0] || {};
  const live = rows.find((row) => row.liveTitle);
  const parsed = rows.find((row) => !NAMED.has(row.status) && row.parts?.title);
  const preview = rows.find((row) => row.previewUrl);
  return {
    title: String(live?.liveTitle || parsed?.parts.title || first.oldStem || ""),
    creator: String(rows.find((row) => row.uploader)?.uploader || ""),
    modelType: String(rows.find((row) => row.modelType)?.modelType || ""),
    previewUrl: String(preview?.previewUrl || ""),
    previewType: preview?.previewType === "video" ? "video" : "image",
    nsfw: rows.some((row) => row.nsfw === true),
    folder: String(first.folder || first.modelPath || ""),
  };
}

const TOKEN = /\s+|[A-Za-z0-9]+|[^A-Za-z0-9\s]/g;

/* The new name in pieces, {text, changed}: words and marks not in the old name
   (a longest common run of tokens, case counts) are changed. */
export function nameDiff(oldStem, newStem) {
  const a = String(oldStem ?? "").match(TOKEN) || [];
  const b = String(newStem ?? "").match(TOKEN) || [];
  const changed = new Array(b.length).fill(true);
  if (a.length * b.length <= 40000) {
    const table = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
    for (let i = a.length - 1; i >= 0; i -= 1) {
      for (let j = b.length - 1; j >= 0; j -= 1) {
        table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
      }
    }
    for (let i = 0, j = 0; i < a.length && j < b.length;) {
      if (a[i] === b[j]) { changed[j] = false; i += 1; j += 1; }
      else if (table[i + 1][j] >= table[i][j + 1]) i += 1;
      else j += 1;
    }
  }
  // A space is part of a change exactly when it sits between two changed tokens.
  const space = (j) => /^\s+$/.test(b[j]);
  const marks = b.map((_token, j) => (space(j) ? changed[j - 1] === true && changed[j + 1] === true && !space(j - 1) && !space(j + 1) : changed[j]));
  const pieces = [];
  b.forEach((token, j) => {
    const last = pieces[pieces.length - 1];
    if (last && last.changed === marks[j]) last.text += token;
    else pieces.push({ text: token, changed: marks[j] });
  });
  return pieces;
}

export function canSelect(row) {
  return Boolean(row && row.status === "change" && !(row.flags || []).some(isBlockingFlag));
}

export function selectedKeys(store) {
  return store.rows.filter((row) => canSelect(row) && store.selected.get(row.key)).map((row) => row.key);
}

/* Removing a copy needs the send2trash package, which a symlinked install may lack.
   An older server that does not say counts as able. */
export function canRemoveDuplicates(store) {
  return store.status?.canRemoveDuplicates !== false;
}

/* The dry run already chose which copy is the extra one: an extra copy starts
   marked for removal when removing is possible at all, the dry run picked the
   kept copy by its name (rows of older dry runs carry no survivorPath), and
   the copies are known to be one file by a Civitai file id or a stored hash. */
export function isSureDuplicate(row) {
  return Boolean(row && row.status === "duplicate" && row.survivorPath
    && (row.sameFileBy === "file" || row.sameFileBy === "hash"));
}

export function defaultRemoveDup(store) {
  if (!canRemoveDuplicates(store)) return new Set();
  return new Set(store.rows.filter(isSureDuplicate).map((row) => row.key));
}

export function duplicateKeys(store) {
  if (!canRemoveDuplicates(store)) return [];
  return store.rows.filter((row) => row.status === "duplicate" && store.removeDup.has(row.key)).map((row) => row.key);
}

export function applyPayload(store) {
  return { jobId: store.jobId, keys: selectedKeys(store), removeDuplicates: duplicateKeys(store) };
}

/* Several files of one version in the list: a version edit is saved per file. */
function isMultiFileVersion(row, rows) {
  return rows.some((other) => other !== row && other.versionId === row.versionId
    && other.fileKey !== row.fileKey && NAMED.has(other.status));
}

/* The decision for an inline edit, or null when nothing changed. Creator and
   title are per model; the version is per version, or per file for a version
   with several files in the list. */
export function editPayload(jobId, row, draft, rows) {
  const parts = row.parts || {};
  const payload = { jobId, modelId: row.modelId };
  let changed = false;
  for (const name of ["creator", "title"]) {
    const next = String(draft[name] ?? "").trim();
    if (next !== String(parts[name] ?? "")) {
      payload[name] = next;
      changed = true;
    }
  }
  const version = String(draft.version ?? "").trim();
  if (version !== String(parts.version ?? "")) {
    payload.version = version;
    payload.versionId = row.versionId;
    if (isMultiFileVersion(row, rows || [])) payload.fileKey = row.fileKey;
    changed = true;
  }
  return changed ? payload : null;
}

/* Rows a decision returned replace the old ones by key; peers whose
   collision state changed come back too. The server does not know which rows
   the user unchecked, so a row that was already selectable keeps the user's
   choice; the server's default applies only to a row that just became
   selectable, and a row that no longer is gets unchecked. */
export function replaceRows(store, rows) {
  for (const row of rows || []) {
    if (!row || !row.key) continue;
    const previous = store.byKey.get(row.key);
    const index = store.rows.findIndex((old) => old.key === row.key);
    if (index >= 0) store.rows[index] = row;
    else store.rows.push(row);
    store.byKey.set(row.key, row);
    if (!canSelect(row)) store.selected.set(row.key, false);
    else if (!(previous && canSelect(previous) && store.selected.has(row.key))) store.selected.set(row.key, Boolean(row.selected));
    if (row.status !== "duplicate") store.removeDup.delete(row.key);
  }
}

function setRows(store, rows) {
  store.rows = Array.isArray(rows) ? rows : [];
  store.byKey = new Map(store.rows.map((row) => [row.key, row]));
  store.selected = new Map(store.rows.map((row) => [row.key, Boolean(row.selected)]));
  store.removeDup = new Set([...store.removeDup].filter((key) => store.byKey.get(key)?.status === "duplicate"));
}

/* The user's choices for one dry run: checkbox overrides as differences from
   the server's default `selected` (selectable rows only), and the extra copies
   marked for removal. {overrides: {key: true|false}, removeDup: [key]} */
export function selectionState(store) {
  const overrides = {};
  for (const row of store.rows) {
    if (!canSelect(row) || !store.selected.has(row.key)) continue;
    const on = Boolean(store.selected.get(row.key));
    if (on !== Boolean(row.selected)) overrides[row.key] = on;
  }
  const removeDup = [...store.removeDup].filter((key) => store.byKey.get(key)?.status === "duplicate");
  return { overrides, removeDup };
}

/* Whether the choices are the defaults (nothing worth storing). */
export function isDefaultSelection(store) {
  const state = selectionState(store);
  if (Object.keys(state.overrides).length) return false;
  const defaults = defaultRemoveDup(store);
  return state.removeDup.length === defaults.size && state.removeDup.every((key) => defaults.has(key));
}

/* Put saved choices back; a row that is gone or no longer selectable stays as it
   is (unchecked). The saved extra copies replace the default ticks: a copy the
   user unticked stays unticked; only rows still listed as extra copies get marked. */
export function restoreSelection(store, saved) {
  for (const [key, on] of Object.entries(saved?.overrides || {})) {
    const row = store.byKey.get(key);
    if (row && canSelect(row)) store.selected.set(key, Boolean(on));
  }
  if (!saved) return;
  store.removeDup = new Set((saved.removeDup || []).filter((key) => store.byKey.get(key)?.status === "duplicate"));
}

/* The saved choices if they belong to this dry run, else null (stored text may be anything). */
export function parseSelection(text, jobId) {
  if (!text || !jobId) return null;
  try {
    const data = JSON.parse(text);
    if (!data || data.jobId !== jobId) return null;
    const overrides = data.overrides ?? {};
    if (!overrides || typeof overrides !== "object" || Array.isArray(overrides)) return null;
    const removeDup = Array.isArray(data.removeDup) ? data.removeDup.filter((key) => typeof key === "string") : [];
    return { overrides, removeDup };
  } catch (_) {
    return null;
  }
}

/* Base models to list in the Base codes section: the saved table plus the
   ones rows flag as new, with the code the naming model suggested. */
export function baseCodeEntries(baseCodes, rows) {
  const entries = new Map();
  for (const [base, code] of Object.entries(baseCodes || {})) entries.set(base, { base, code: String(code), isNew: false });
  for (const row of rows || []) {
    if (!row.baseModel || !(row.flags || []).includes("new-base-code") || entries.has(row.baseModel)) continue;
    entries.set(row.baseModel, { base: row.baseModel, code: String(row.parts?.base || ""), isNew: true });
  }
  return [...entries.values()].sort((a, b) => Number(b.isNew) - Number(a.isNew) || a.base.localeCompare(b.base));
}

export function fileName(path) {
  const text = String(path || "");
  const i = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
  return i >= 0 ? text.slice(i + 1) : text;
}

function count(n) {
  return Number(n || 0).toLocaleString("en-US");
}

function plural(n, one, many) {
  return `${count(n)} ${n === 1 ? one : many || `${one}s`}`;
}

function readLastJob() {
  try {
    return window.localStorage.getItem(LAST_JOB_KEY) || null;
  } catch (_) {
    return null;
  }
}

function writeLastJob(jobId) {
  try {
    if (jobId) window.localStorage.setItem(LAST_JOB_KEY, jobId);
    else window.localStorage.removeItem(LAST_JOB_KEY);
  } catch (_) {
    // per-browser convenience only
  }
}

/* Checkbox and remove-copy choices of one dry run, so a reload or a ComfyUI restart keeps them. */
function readSelection(jobId) {
  try {
    return parseSelection(window.localStorage.getItem(SELECTION_KEY), jobId);
  } catch (_) {
    return null;
  }
}

/* A null state (the defaults) removes the stored choices. */
function writeSelection(jobId, state) {
  try {
    if (jobId && state) {
      window.localStorage.setItem(SELECTION_KEY, JSON.stringify({ jobId, overrides: state.overrides, removeDup: state.removeDup }));
    } else {
      window.localStorage.removeItem(SELECTION_KEY);
    }
  } catch (_) {
    // per-browser convenience only
  }
}

function lookText(n) {
  return `${count(n)} ${n === 1 ? "model needs" : "models need"} a look`;
}

function formatTime(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? String(iso || "") : date.toLocaleString();
}

/* ---------------------------------------------------------------- the view */

/* deps: { store, getJson, postJson, escapeHtml, startJob(endpoint, type, payload, statusText),
   activeJob() -> {jobId, type, status} | null, setStatus(text), adoptNamingModel(name),
   setNamingModels(list), matureMode() -> "show" | "blur" | "hide", openPreview(item) } */
export function renderRenameView(container, deps) {
  const store = deps.store;
  const esc = (text) => deps.escapeHtml(text == null ? "" : String(text));
  injectRenameStyles();
  container.classList.add("cu-rn");

  const otherJob = () => {
    const job = deps.activeJob();
    return job && job.jobId ? job : null;
  };
  const locked = () => store.busy || Boolean(otherJob()) || store.applied || store.runActive;

  function notify(text, level = "info") {
    store.message = text ? { text: String(text), level } : null;
  }

  /* One request at a time: every control is disabled while one is in flight. */
  async function request(fn) {
    if (store.busy) return undefined;
    store.busy = true;
    render();
    try {
      return await fn();
    } catch (error) {
      notify(error?.message || String(error), "error");
      return undefined;
    } finally {
      store.busy = false;
      render();
    }
  }

  async function loadStatus() {
    try {
      store.status = await deps.getJson(`${ROUTE}/rename/status`);
      store.statusError = "";
      deps.setNamingModels?.(store.status.namingModels || []);
    } catch (error) {
      store.statusError = error?.message || String(error);
    }
  }

  async function loadRows() {
    if (!store.jobId) return;
    try {
      const data = await deps.getJson(`${ROUTE}/rename/rows?jobId=${encodeURIComponent(store.jobId)}`);
      setRows(store, data.rows);
      store.removeDup = defaultRemoveDup(store);
      const saved = readSelection(store.jobId);
      if (saved) restoreSelection(store, saved);
      else writeSelection(null); // choices of another dry run, if any, are dropped
      store.summary = data.summary || null;
      store.errors = Array.isArray(data.errors) ? data.errors : [];
      store.runStatus = data.status || "";
      store.applied = Boolean(data.applied);
      store.restoredFrom = data.restoredFrom || null;
      store.loadedJobId = store.jobId;
      if (store.applied) {
        // Read-only: nothing is left to choose for this dry run.
        store.removeDup.clear();
        store.confirming = false;
        store.editing = null;
      }
    } catch (error) {
      if (/job not found/i.test(String(error?.message || ""))) {
        // ComfyUI was restarted: that dry run is gone.
        forgetDryRun();
      } else {
        notify(`Could not load the dry run: ${error?.message || error}`, "error");
      }
    }
  }

  function forgetDryRun() {
    store.forgottenJobId = store.jobId;
    store.jobId = null;
    store.loadedJobId = null;
    setRows(store, []);
    store.summary = null;
    store.applied = false;
    store.restoredFrom = null;
    writeLastJob(null);
    writeSelection(null);
  }

  function saveSelection() {
    if (store.jobId && store.loadedJobId === store.jobId && !store.applied) {
      writeSelection(store.jobId, isDefaultSelection(store) ? null : selectionState(store));
    }
  }

  async function refresh() {
    if (!store.restoredLastJob) {
      store.restoredLastJob = true;
      if (!store.jobId) store.jobId = readLastJob();
    }
    await loadStatus();
    const job = otherJob();
    if (job && job.type === DRY_RUN_JOB) {
      store.jobId = job.jobId;
      store.runActive = true;
    } else if (store.runActive) {
      // The panel lost the job (ComfyUI restarted): whatever the server still has is reloaded.
      store.runActive = false;
      store.runLine = "";
      store.loadedJobId = null;
      await loadRows();
    } else {
      const saved = store.status?.lastDryRun;
      if (!store.jobId && saved && saved !== store.forgottenJobId) {
        // No dry run of this browser's own: show the one the server kept across a restart.
        store.jobId = saved;
        writeLastJob(saved);
      }
      if (store.jobId && store.loadedJobId !== store.jobId) await loadRows();
    }
    render();
  }

  /* ---- actions ---- */

  async function startDryRun() {
    if (store.busy || otherJob() || store.runActive) return;
    const codes = store.status?.baseCodes || {};
    await request(async () => {
      const data = await deps.startJob(`${ROUTE}/jobs/rename-dry-run`, DRY_RUN_JOB, {}, "Dry run: collecting your models\u2026");
      store.baseCodesEmptyBeforeRun = Object.keys(codes).length === 0;
      store.jobId = data.jobId;
      store.loadedJobId = null;
      store.runActive = true;
      store.runLine = "Starting\u2026";
      store.applied = false;
      store.restoredFrom = null;
      store.persistWarning = "";
      store.result = null;
      store.recoverResult = null;
      store.revealError = null;
      store.editing = null;
      store.confirming = false;
      store.drafts.clear();
      store.limits = {};
      setRows(store, []);
      store.removeDup.clear();
      writeLastJob(data.jobId);
      writeSelection(null);
      notify(null);
    });
  }

  async function decide(payload, done) {
    if (locked()) return;
    await request(async () => {
      const data = await deps.postJson(`${ROUTE}/rename/decision`, payload);
      replaceRows(store, data.rows);
      saveSelection();
      if (done) done();
      notify(null);
    });
  }

  async function applySelected() {
    if (locked()) return;
    const payload = applyPayload(store);
    if (!payload.keys.length && !payload.removeDuplicates.length) return;
    await request(async () => {
      const data = await deps.startJob(`${ROUTE}/jobs/rename-apply`, APPLY_JOB, payload, "Applying the renames\u2026");
      // The server could not save that this dry run was applied (it deleted its saved copy instead).
      if (data?.persistWarning) store.persistWarning = String(data.persistWarning);
      store.confirming = false;
      store.result = null;
      store.runLine = "Applying\u2026";
      notify(null);
    });
  }

  async function recover(action) {
    if (store.busy || otherJob()) return;
    await request(async () => {
      const data = await deps.postJson(`${ROUTE}/rename/recover`, { action });
      if (data?.persistWarning) store.persistWarning = String(data.persistWarning);
      store.recoverResult = { action, done: data.done, skipped: Array.isArray(data.skipped) ? data.skipped : [] };
      if (store.status) store.status.journal = data.journal || { pending: false, steps: 0 };
      await loadStatus();
      // Finish moved files: the Updates view's results now carry the new paths.
      await deps.reloadResults?.();
    });
  }

  /* Show a row's file in the file manager; an error shows on the row's line. */
  async function reveal(row) {
    if (!row || !store.jobId) return;
    try {
      await deps.postJson(`${ROUTE}/rename/reveal`, { jobId: store.jobId, key: row.key });
      if (store.revealError?.key === row.key) store.revealError = null;
    } catch (error) {
      store.revealError = { key: row.key, text: error?.message || String(error) };
    }
    render();
  }

  async function downloadModel() {
    if (store.busy || otherJob()) return;
    await request(async () => {
      const data = await deps.startJob(`${ROUTE}/jobs/download-naming-model`, DOWNLOAD_JOB, {}, "Downloading the naming model\u2026");
      if (!data.jobId) {
        if (data.namingModel) deps.adoptNamingModel?.(data.namingModel);
        notify(data.reused
          ? `Found ${data.namingModel || "the naming model"} in your text encoder folders. It is now the naming model; nothing was downloaded.`
          : "Nothing to download.");
        await loadStatus();
      } else {
        store.runLine = "Downloading\u2026";
        notify(null);
      }
    });
  }

  async function saveBaseCode(base) {
    if (store.busy) return;
    // An untouched entry saves the code it shows (the AI's suggestion for a new base).
    const shown = baseCodeEntries(store.status?.baseCodes, store.rows).find((entry) => entry.base === base);
    const code = String(store.baseDrafts.has(base) ? store.baseDrafts.get(base) : shown?.code ?? "").trim();
    if (!code) {
      notify("A base code cannot be empty.", "error");
      render();
      return;
    }
    await request(async () => {
      const data = await deps.postJson(`${ROUTE}/rename/base-code`, { baseModel: base, code });
      if (store.status) store.status.baseCodes = data.baseCodes || {};
      store.baseDrafts.delete(base);
      notify(`Saved "${code}" for ${base}. The names use it after the next dry run.`);
    });
  }

  /* ---- job hooks (called by civitai_updater.js on every poll tick) ---- */

  async function onJob(job, finished) {
    if (!job || !RENAME_JOB_TYPES.includes(job.type)) return;
    const line = job.message || (job.total ? `${job.progress || 0} of ${job.total}` : "");
    if (!finished) {
      const firstSight = job.type === DRY_RUN_JOB && (store.jobId !== job.jobId || !store.runActive);
      store.runLine = line;
      if (job.type === DRY_RUN_JOB) {
        store.jobId = job.jobId;
        store.runActive = true;
        writeLastJob(job.jobId);
      }
      if (firstSight) render();
      else updateRunLine();
      return;
    }
    store.runLine = "";
    const failure = Array.isArray(job.errors) && job.errors.length ? job.errors[0] : "Unknown error";
    if (job.type === DRY_RUN_JOB) {
      store.runActive = false;
      store.jobId = job.jobId;
      store.loadedJobId = null;
      await loadStatus();
      await loadRows();
      const codes = Object.keys(store.status?.baseCodes || {}).length;
      if (store.baseCodesEmptyBeforeRun && codes) store.baseOpen = true;
      store.baseCodesEmptyBeforeRun = null;
      if (job.status === "completed") {
        const look = groupCards(store.rows).look.length;
        notify(`Dry run done: ${plural(store.rows.length, "entry", "entries")}, ${lookText(look)}.`);
      } else if (job.status === "cancelled") {
        notify("Dry run stopped. The entries named so far are listed.");
      } else {
        notify(`The dry run failed: ${failure}`, "error");
      }
    } else if (job.type === APPLY_JOB) {
      store.result = { status: job.status, summary: job.summary || {}, error: job.status === "failed" ? failure : "" };
      await loadStatus();
      await loadRows();
      // The server patched the check results to the new paths; fetch them again.
      await deps.reloadResults?.();
      if (job.status === "cancelled") notify("Apply stopped. What was done is listed below.");
      else if (job.status === "failed") notify(`Apply failed: ${failure}`, "error");
      else notify(null);
    } else if (job.type === DOWNLOAD_JOB) {
      if (job.status === "completed" && job.summary?.namingModel) {
        deps.adoptNamingModel?.(job.summary.namingModel);
        notify(`Downloaded ${job.summary.namingModel}. It is now the naming model.`);
      } else if (job.status === "cancelled") {
        notify("Download stopped. The next download starts over.");
      } else if (job.status === "failed") {
        notify(`The download failed: ${failure}`, "error");
      }
      await loadStatus();
    }
    // Without a message of its own, the poller's "Done" stays in the status line.
    if (store.message) deps.setStatus?.(store.message.text);
    render();
  }

  function updateRunLine() {
    const el = container.querySelector("[data-rn-run]");
    if (el) el.textContent = store.runLine || "";
    else render();
  }

  /* ---- rendering ---- */

  // The sections and cards of the last render, for in-place updates and clicks.
  let shownSections = null;
  let cardsById = new Map();

  function render() {
    shownSections = null;
    cardsById = new Map();
    container.innerHTML = buildHtml();
  }

  function buildHtml() {
    const status = store.status;
    const html = [];
    const journal = status?.journal;
    if (journal?.pending) {
      html.push(`<div class="cu-rn-banner" data-level="warn">
        <div class="cu-rn-banner-text">A rename was interrupted: ${esc(plural(journal.steps, "step"))} left.</div>
        <div class="cu-rn-actions">
          <button class="cu-btn cu-btn-sm" data-rn-action="recover-finish"${disabledIf(store.busy || otherJob())}>Finish</button>
          <button class="cu-btn cu-btn-sm cu-btn-ghost" data-rn-action="recover-undo"${disabledIf(store.busy || otherJob())}>Undo</button>
        </div>
      </div>`);
      html.push(messageHtml());
      html.push(recoverHtml());
      return html.join("");
    }
    html.push(recoverHtml());

    if (!status) {
      html.push(`<div class="cu-empty">${store.statusError ? esc(`Could not reach the renamer: ${store.statusError}`) : "Loading\u2026"}</div>`);
      return html.join("");
    }

    if (!status.available) {
      const canDownload = status.reason && !/Generate Text/i.test(status.reason);
      html.push(`<div class="cu-rn-banner" data-level="info">
        <div class="cu-rn-banner-text">${esc(status.reason || "The renamer is not available.")}</div>
        ${canDownload ? `<div class="cu-rn-actions">
          <button class="cu-btn cu-btn-sm" data-rn-action="download"${disabledIf(store.busy || otherJob())}>Download naming model (9.75 GB)</button>
        </div>
        ${(status.namingModels || []).length ? `<div class="cu-rn-hint">Or choose one of your ${plural(status.namingModels.length, "text encoder")} in Settings \u203a Civitai Updater \u203a Rename.</div>` : ""}` : ""}
      </div>`);
    }

    html.push(messageHtml());
    html.push(runBarHtml());
    html.push(resultHtml());

    if (store.rows.length) {
      const sections = groupCards(store.rows);
      shownSections = sections;
      cardsById = new Map(Object.values(sections).flat().map((card) => [card.id, card]));
      html.push(headHtml());
      if (store.applied) {
        html.push(`<div class="cu-rn-banner" data-level="info"><div class="cu-rn-banner-text">These names were applied. Run a new dry run to see your files as they are now.</div></div>`);
      } else if (store.restoredFrom) {
        html.push(`<div class="cu-rn-banner" data-level="info"><div class="cu-rn-banner-text">Dry run from ${esc(formatTime(store.restoredFrom))}, restored after a restart. Files changed since are skipped when you apply.</div></div>`);
      }
      if (store.confirming) html.push(confirmHtml());
      if (!canRemoveDuplicates(store) && store.rows.some((row) => row.status === "duplicate")) {
        html.push(`<div class="cu-rn-hint">${esc(NO_SEND2TRASH)}</div>`);
      }
      html.push(sectionHtml("look", "Needs a look", sections.look, true));
      html.push(sectionHtml("change", "Changes", sections.change, true));
      html.push(sectionHtml("unchanged", "Unchanged", sections.unchanged, store.open.unchanged));
      html.push(sectionHtml("kept", "Kept, not renamed", sections.kept, store.open.kept));
    } else if (store.jobId && !store.runActive && store.loadedJobId === store.jobId && store.runStatus === "completed") {
      html.push(`<div class="cu-empty">This dry run found nothing to rename.</div>`);
    }
    html.push(baseCodesHtml());
    return html.join("");
  }

  function disabledIf(condition) {
    return condition ? " disabled" : "";
  }

  function messageHtml() {
    const warning = store.persistWarning
      ? `<div class="cu-rn-banner" data-level="warn"><div class="cu-rn-banner-text">${esc(store.persistWarning)}</div></div>`
      : "";
    if (!store.message) return warning;
    return warning + `<div class="cu-rn-message" data-level="${store.message.level === "error" ? "error" : "info"}">${esc(store.message.text)}
      <button class="cu-text-btn" data-rn-action="dismiss" title="Dismiss">\u00d7</button></div>`;
  }

  function runBarHtml() {
    const status = store.status;
    const job = otherJob();
    const running = store.runActive || (job && RENAME_JOB_TYPES.includes(job.type));
    const label = store.rows.length || store.applied ? "Run a new dry run" : "Start dry run";
    const blockedBy = job && !RENAME_JOB_TYPES.includes(job.type) ? "Another job is running." : "";
    return `<div class="cu-rn-runbar">
      <button class="cu-btn cu-btn-sm${store.rows.length ? "" : " cu-btn-primary"}" data-rn-action="dry-run"${disabledIf(!status?.available || store.busy || job)}>${label}</button>
      <span class="cu-rn-run" data-rn-run>${esc(running ? store.runLine : blockedBy)}</span>
    </div>
    <div class="cu-rn-hint">The dry run only proposes names; nothing on disk changes until you apply.</div>`;
  }

  function headHtml() {
    const sections = shownSections || groupCards(store.rows);
    const changes = store.rows.filter((row) => row.status === "change").length;
    const keys = selectedKeys(store);
    const removals = duplicateKeys(store);
    const n = keys.length + removals.length;
    return `<div class="cu-rn-head" data-rn-head>
      <div class="cu-rn-counts">Rename · ${plural(store.rows.length, "entry", "entries")} · ${count(changes)} change · <b>${lookText(sections.look.length)}</b></div>
      ${store.applied ? "" : `<button class="cu-btn cu-btn-sm cu-btn-primary" data-rn-action="confirm"${disabledIf(!n || locked() || store.confirming)}>Apply ${count(n)} selected</button>`}
    </div>`;
  }

  function confirmHtml() {
    const renames = selectedKeys(store).length;
    const removals = duplicateKeys(store).length;
    const parts = [];
    if (renames) parts.push(`rename ${plural(renames, "file")} (with their sidecars and previews)`);
    if (removals) parts.push(`move ${plural(removals, "extra copy", "extra copies")} to the Recycle Bin`);
    return `<div class="cu-rn-banner" data-level="warn">
      <div class="cu-rn-banner-text">This will ${esc(parts.join(" and "))}. Models loaded right now are skipped.</div>
      <div class="cu-rn-actions">
        <button class="cu-btn cu-btn-sm cu-btn-primary" data-rn-action="apply"${disabledIf(locked())}>Apply</button>
        <button class="cu-btn cu-btn-sm cu-btn-ghost" data-rn-action="cancel-confirm"${disabledIf(store.busy)}>Cancel</button>
      </div>
    </div>`;
  }

  function resultHtml() {
    const result = store.result;
    if (!result) return "";
    const s = result.summary || {};
    const renamed = Array.isArray(s.renamed) ? s.renamed.length : Number(s.renamed || 0);
    const removed = Array.isArray(s.removed) ? s.removed.length : Number(s.removed || 0);
    const skipped = Array.isArray(s.skipped) ? s.skipped : [];
    const warnings = Array.isArray(s.warnings) ? s.warnings : [];
    const head = `Renamed ${plural(renamed, "file")} \u00b7 removed ${plural(removed, "extra copy", "extra copies")} \u00b7 skipped ${count(skipped.length)}`;
    const list = skipped.map((item) => `<li><span class="cu-rn-mono">${esc(fileName(item.path || item.entry))}</span>: ${esc(item.reason || "skipped")}</li>`).join("");
    const warn = warnings.map((text) => `<li>${esc(text)}</li>`).join("");
    return `<div class="cu-rn-result">
      <div class="cu-rn-result-head">${esc(head)}</div>
      ${result.error ? `<div class="cu-rn-flag" data-level="block">${esc(result.error)}</div>` : ""}
      ${list ? `<div class="cu-label">Skipped</div><ul class="cu-rn-list">${list}</ul>` : ""}
      ${warn ? `<div class="cu-label">Warnings</div><ul class="cu-rn-list">${warn}</ul>` : ""}
      <button class="cu-text-btn" data-rn-action="dismiss-result">Dismiss</button>
    </div>`;
  }

  function recoverHtml() {
    const r = store.recoverResult;
    if (!r) return "";
    const done = Array.isArray(r.done) ? r.done.length : Number(r.done || 0);
    const verb = r.action === "undo" ? "Undid" : "Finished";
    const list = r.skipped.map((item) => `<li><span class="cu-rn-mono">${esc(fileName(item.path || item.entry))}</span>: ${esc(item.reason || "skipped")}</li>`).join("");
    return `<div class="cu-rn-result">
      <div class="cu-rn-result-head">${esc(`${verb} ${plural(done, "entry", "entries")}${r.skipped.length ? `, ${count(r.skipped.length)} skipped` : ""}.`)}</div>
      ${list ? `<ul class="cu-rn-list">${list}</ul>` : ""}
      <button class="cu-text-btn" data-rn-action="dismiss-recover">Dismiss</button>
    </div>`;
  }

  function toggleHtml(id, cards) {
    if (id !== "change" && id !== "look") return "";
    const selectable = cards.flatMap((card) => card.rows).filter(canSelect);
    if (!selectable.length) return "";
    const allOn = selectable.every((row) => store.selected.get(row.key));
    return `<button class="cu-text-btn" data-rn-action="select-all" data-rn-section="${id}" data-rn-on="${allOn ? "0" : "1"}"${disabledIf(locked())}>${allOn ? "Select none" : "Select all"}</button>`;
  }

  function sectionHtml(id, title, cards, open) {
    if (!cards.length) return "";
    const limit = store.limits[id] || PAGE;
    const shown = cards.slice(0, limit);
    const hideable = id === "unchanged" || id === "kept";
    const body = open
      ? `<div class="cu-rn-cards">${shown.map(cardHtml).join("")}</div>
        ${cards.length > limit ? `<button class="cu-text-btn cu-rn-more" data-rn-action="more" data-rn-section="${id}">Show ${count(Math.min(PAGE, cards.length - limit))} more of ${count(cards.length - limit)}</button>` : ""}`
      : "";
    return `<section class="cu-rn-section" data-rn-id="${id}">
      <div class="cu-rn-section-head">
        ${hideable
          ? `<button class="cu-rn-section-toggle" data-rn-action="toggle-section" data-rn-section="${id}" aria-expanded="${open}"><span class="cu-group-caret">${open ? "▾" : "▸"}</span>${esc(title)} <span class="cu-rn-section-count">${count(cards.length)}${open ? "" : " · hidden"}</span></button>`
          : `<span class="cu-rn-section-title">${esc(title)} <span class="cu-rn-section-count">${count(cards.length)}</span></span>`}
        ${open ? `<span data-rn-toggle="${id}">${toggleHtml(id, cards)}</span>` : ""}
      </div>
      ${body}
    </section>`;
  }

  /* One model, like a card in Updates: thumbnail, title, uploader, type, and one line per file. */
  function cardHtml(card) {
    const info = cardInfo(card);
    const mature = info.nsfw ? String(deps.matureMode?.() || "show") : "show";
    const url = mature === "hide" ? "" : info.previewUrl;
    let thumb = `<div class="cu-thumb-empty">No preview</div>`;
    if (url && info.previewType === "video") thumb = `<video src="${esc(url)}#t=0.5" preload="metadata" muted playsinline></video>`;
    else if (url) thumb = `<img src="${esc(url)}" alt="" loading="lazy" decoding="async">`;
    const zoom = url && deps.openPreview ? ` data-rn-action="preview"` : "";
    const type = info.modelType
      ? `<span class="cu-type-pill" data-type="${esc(info.modelType)}">${esc(info.modelType.charAt(0).toUpperCase() + info.modelType.slice(1))}</span>`
      : "<span></span>";
    const keys = new Set(card.rows.map((row) => row.key));
    const isLocked = locked();
    return `<article class="cu-item cu-rn-card" data-rn-card="${esc(card.id)}"${mature === "blur" ? ' data-mature="blur"' : ""}>
      <div class="cu-thumb"${zoom}>${thumb}</div>
      <div class="cu-item-body">
        <div class="cu-item-header">
          <div class="cu-ver-row cu-ver-row-model">
            <span class="cu-ver-label cu-ver-label-model">${type}</span>
            <span class="cu-ver-date"></span>
            <span class="cu-ver-base"></span>
            <div class="cu-ver-main">
              <h4 title="${esc(info.folder)}">${esc(info.title)}</h4>
              ${info.creator ? `<span class="cu-creator">by ${esc(info.creator)}</span>` : ""}
            </div>
          </div>
        </div>
        <div class="cu-versions">${card.rows.map((row) => fileHtml(row, keys, isLocked)).join("")}</div>
      </div>
    </article>`;
  }

  /* One file of a card: a .cu-ver-row line (checkbox, label, base code, names,
     buttons), then one muted line per flag or reason, then the edit form. */
  function fileHtml(row, keys, isLocked) {
    const named = NAMED.has(row.status);
    const dup = row.status === "duplicate";
    const canRemove = canRemoveDuplicates(store);
    const removing = dup && canRemove && store.removeDup.has(row.key);
    let check = `<span class="cu-rn-nocheck"></span>`;
    if (row.status === "change") {
      const selectable = canSelect(row);
      const on = selectable && store.selected.get(row.key);
      check = `<input type="checkbox" data-rn-select${on ? " checked" : ""}${disabledIf(!selectable || isLocked)} title="${selectable ? "Rename this file on Apply" : "Fix the problems below first"}">`;
    } else if (dup) {
      check = `<input type="checkbox" data-rn-dup${removing ? " checked" : ""}${disabledIf(isLocked || !canRemove)} title="${canRemove ? "Remove this extra copy on Apply" : esc(NO_SEND2TRASH)}">`;
    }
    let role = row.metadataOnly ? "metadata" : "saved";
    let label = row.metadataOnly ? "Metadata" : "Saved";
    if (dup) [role, label] = ["extra", "Extra copy"];
    else if (row.status === "error") [role, label] = ["error", "Error"];
    const old = `<span class="cu-ver-link cu-copy-path cu-rn-old" data-rn-action="reveal" title="${esc(`${row.modelPath || ""}\nClick to show in folder`)}">${esc(row.oldStem)}</span>`;
    const renamed = row.status === "change"
      ? `<span class="cu-rn-new" title="${esc(row.newStem)}"><span class="cu-rn-arrow">→</span>${nameDiff(row.oldStem, row.newStem)
        .map((piece) => (piece.changed ? `<mark class="cu-rn-diff">${esc(piece.text)}</mark>` : esc(piece.text))).join("")}</span>`
      : "";
    const actions = [];
    if (named) {
      actions.push(`<button class="cu-inline-btn" data-rn-action="edit"${disabledIf(isLocked)}>Edit</button>`);
      if (row.status === "change") actions.push(`<button class="cu-inline-btn" data-rn-action="keep"${disabledIf(isLocked)} title="Don't rename this file">Keep current name</button>`);
      if (!(row.flags || []).includes("ask-again")) actions.push(`<button class="cu-inline-btn" data-rn-action="ask-again"${disabledIf(isLocked)} title="Forget the saved name; the naming model names this model again on the next dry run">Ask again</button>`);
    } else if (row.status === "kept" && row.reason === "kept-by-you") {
      actions.push(`<button class="cu-inline-btn" data-rn-action="unkeep"${disabledIf(isLocked)} title="Rename this file after all">Undo keep current name</button>`);
    }
    const messages = [];
    if (named) {
      for (const flag of row.flags || []) messages.push(flagHtml(flag, row, isLocked));
    } else if (dup) {
      // The kept copy is the line above; only an older dry run may lack it here.
      if (!keys.has(row.survivorKey)) messages.push(`<div class="cu-issue-msg">${esc(reasonText(row, store.byKey))}</div>`);
      if (row.nameMatchesInfo === false) messages.push(`<div class="cu-issue-msg" data-level="warn">This name doesn't match its Civitai info.</div>`);
    } else {
      messages.push(`<div class="cu-issue-msg"${row.status === "error" ? ' data-level="block"' : ""}>${esc(reasonText(row, store.byKey))}</div>`);
    }
    if (store.revealError?.key === row.key) messages.push(`<div class="cu-issue-msg" data-level="block">${esc(store.revealError.text)}</div>`);
    const quiet = row.status === "unchanged" || row.status === "kept";
    // A line that needs a look shows its buttons; the others show them on hover.
    const attention = messages.length > 0 || store.editing === row.key;
    const base = row.parts?.base || "—";
    return `<div class="cu-rn-file${quiet ? " is-quiet" : ""}" data-rn-key="${esc(row.key)}" data-status="${esc(row.status)}"${removing ? " data-removing" : ""}${attention ? " data-attention" : ""}>
      <div class="cu-ver-row cu-rn-line">
        <label class="cu-rn-check">${check}</label>
        <span class="cu-ver-label" data-role="${role}">${label}</span>
        <span class="cu-ver-base" title="${esc(base)}">${esc(base)}</span>
        <span class="cu-ver-main"><span class="cu-rn-names">${old}${renamed}</span>${actions.length ? `<span class="cu-rn-line-actions">${actions.join("")}</span>` : ""}</span>
      </div>
      ${messages.join("")}
      ${store.editing === row.key ? editHtml(row) : ""}
    </div>`;
  }

  function flagHtml(flag, row, isLocked) {
    const info = flagInfo(flag, row, store.rows);
    let action = "";
    if (info.action === "use-maker" && row.parts?.creator) {
      action = ` <button class="cu-inline-btn" data-rn-action="use-maker"${disabledIf(isLocked)}>Use ${esc(row.parts.creator)}</button>`;
      if (row.uploader && row.uploader.toLowerCase() !== row.parts.creator) {
        action += ` <button class="cu-inline-btn" data-rn-action="use-uploader"${disabledIf(isLocked)}>Use ${esc(row.uploader)}</button>`;
      }
    } else if (info.action === "base-link") {
      action = ` <button class="cu-text-btn" data-rn-action="base-link" data-rn-base="${esc(row.baseModel || "")}">Base codes</button>`;
    }
    return `<div class="cu-issue-msg" data-level="${info.level}">${esc(info.text)}${action}</div>`;
  }

  /* A checkbox changed: update the Apply count and the Select all/none toggles in
     place, so the cards (and their images) are not built again. */
  function updateSelectionUi() {
    const head = container.querySelector("[data-rn-head]");
    if (store.confirming || !head || !shownSections) {
      render();
      return;
    }
    head.outerHTML = headHtml();
    for (const el of container.querySelectorAll("[data-rn-toggle]")) {
      el.innerHTML = toggleHtml(el.dataset.rnToggle, shownSections[el.dataset.rnToggle] || []);
    }
  }

  /* Lightbox of the card's previews, the same one Updates opens. */
  function openPreview(card) {
    if (!card || !deps.openPreview) return;
    const info = cardInfo(card);
    const seen = new Set();
    const localVersions = [];
    for (const row of card.rows) {
      if (!row.previewUrl || seen.has(row.previewUrl)) continue;
      seen.add(row.previewUrl);
      localVersions.push({ previewUrl: row.previewUrl, previewType: row.previewType, versionName: row.oldStem, baseModel: row.baseModel || "" });
    }
    deps.openPreview({ modelName: info.title, creatorName: info.creator, localVersions });
  }

  function editHtml(row) {
    const draft = store.drafts.get(row.key) || { ...row.parts };
    const field = (name, label, hint) => `<label class="cu-rn-field"><span>${label}</span>
      <input type="text" data-rn-draft="${name}" value="${esc(draft[name] ?? "")}" spellcheck="false" autocomplete="off"${disabledIf(store.busy)}>
      <em>${hint}</em></label>`;
    return `<div class="cu-rn-edit">
      ${field("creator", "Creator", "every version of this model")}
      ${field("title", "Title", "every version of this model")}
      ${field("version", "Version", "this version only")}
      <div class="cu-rn-actions">
        <button class="cu-btn cu-btn-sm" data-rn-action="save-edit"${disabledIf(locked())}>Save</button>
        <button class="cu-btn cu-btn-sm cu-btn-ghost" data-rn-action="cancel-edit">Cancel</button>
      </div>
    </div>`;
  }

  function baseCodesHtml() {
    const entries = baseCodeEntries(store.status?.baseCodes, store.rows);
    if (!entries.length) return "";
    const items = entries.map((entry) => {
      const draft = store.baseDrafts.has(entry.base) ? store.baseDrafts.get(entry.base) : entry.code;
      const dirty = entry.isNew || draft !== entry.code;
      return `<div class="cu-rn-base" data-rn-base-row="${esc(entry.base)}">
        <span class="cu-rn-base-name">${esc(entry.base)}${entry.isNew ? ` <span class="cu-rn-chip">new</span>` : ""}</span>
        <span class="cu-rn-arrow">\u2192</span>
        <input type="text" data-rn-base-input="${esc(entry.base)}" value="${esc(draft)}" spellcheck="false" autocomplete="off"${disabledIf(store.busy)}>
        <button class="cu-inline-btn" data-rn-action="save-base" data-rn-base="${esc(entry.base)}"${disabledIf(store.busy || !dirty)}>Save</button>
      </div>`;
    }).join("");
    return `<details class="cu-rn-bases" data-rn-bases${store.baseOpen ? " open" : ""}>
      <summary>Base codes <span class="cu-rn-section-count">${count(entries.length)}</span></summary>
      <div class="cu-rn-hint">The short code in brackets at the end of each name. Saved codes are used from the next dry run on.</div>
      ${items}
    </details>`;
  }

  /* ---- events ---- */

  if (!container.dataset.rnBound) {
    container.dataset.rnBound = "1";
    container.addEventListener("click", onClick);
    container.addEventListener("change", onChange);
    container.addEventListener("input", onInput);
    container.addEventListener("keydown", onKey);
    container.addEventListener("error", onMediaError, true); // load errors do not bubble
    container.addEventListener("toggle", (ev) => {
      if (ev.target instanceof HTMLDetailsElement && ev.target.hasAttribute("data-rn-bases")) store.baseOpen = ev.target.open;
    }, true);
  }

  function rowOf(el) {
    const host = el.closest("[data-rn-key]");
    return host ? store.byKey.get(host.dataset.rnKey) : null;
  }

  function onClick(ev) {
    const target = ev.target instanceof Element ? ev.target.closest("[data-rn-action]") : null;
    if (!target || target.disabled) return;
    const action = target.dataset.rnAction;
    const row = rowOf(target);
    switch (action) {
      case "dismiss": notify(null); render(); break;
      case "dismiss-result": store.result = null; render(); break;
      case "dismiss-recover": store.recoverResult = null; render(); break;
      case "recover-finish": recover("finish"); break;
      case "recover-undo": recover("undo"); break;
      case "download": downloadModel(); break;
      case "dry-run": startDryRun(); break;
      case "confirm":
        if (!locked()) { store.confirming = true; render(); }
        break;
      case "cancel-confirm": store.confirming = false; render(); break;
      case "apply": applySelected(); break;
      case "toggle-section": {
        const id = target.dataset.rnSection;
        store.open[id] = !store.open[id];
        render();
        break;
      }
      case "more": {
        const id = target.dataset.rnSection;
        store.limits[id] = (store.limits[id] || PAGE) + PAGE;
        render();
        break;
      }
      case "select-all": {
        const on = target.dataset.rnOn === "1";
        const cards = groupCards(store.rows)[target.dataset.rnSection] || [];
        for (const card of cards) for (const r of card.rows) if (canSelect(r)) store.selected.set(r.key, on);
        saveSelection();
        render();
        break;
      }
      case "edit":
        if (row) {
          store.editing = store.editing === row.key ? null : row.key;
          if (store.editing) store.drafts.set(row.key, { creator: row.parts?.creator || "", title: row.parts?.title || "", version: row.parts?.version || "" });
          render();
          container.querySelector(`[data-rn-key="${cssEscape(row.key)}"] [data-rn-draft]`)?.focus();
        }
        break;
      case "cancel-edit":
        if (row) store.drafts.delete(row.key);
        store.editing = null;
        render();
        break;
      case "save-edit": saveEdit(row); break;
      case "keep": if (row) decide({ jobId: store.jobId, modelId: row.modelId, fileKey: row.fileKey, keep: true }); break;
      case "unkeep": if (row) decide({ jobId: store.jobId, modelId: row.modelId, fileKey: row.fileKey, keep: false }); break;
      case "ask-again": if (row) decide({ jobId: store.jobId, modelId: row.modelId, askAgain: true }); break;
      case "use-maker":
        if (row) decide({ jobId: store.jobId, modelId: row.modelId, creator: row.parts?.creator || "" });
        break;
      case "use-uploader":
        if (row && row.uploader) decide({ jobId: store.jobId, modelId: row.modelId, creator: row.uploader });
        break;
      case "reveal": reveal(row); break;
      case "preview": openPreview(cardsById.get(target.closest("[data-rn-card]")?.dataset.rnCard)); break;
      case "base-link": openBaseCode(target.dataset.rnBase || ""); break;
      case "save-base": saveBaseCode(target.dataset.rnBase || ""); break;
      default: break;
    }
  }

  function saveEdit(row) {
    if (!row || locked()) return;
    const draft = store.drafts.get(row.key) || {};
    const payload = editPayload(store.jobId, row, draft, store.rows);
    if (!payload) {
      store.editing = null;
      store.drafts.delete(row.key);
      render();
      return;
    }
    decide(payload, () => {
      store.editing = null;
      store.drafts.delete(row.key);
    });
  }

  function openBaseCode(base) {
    store.baseOpen = true;
    render();
    const details = container.querySelector("[data-rn-bases]");
    const input = base ? container.querySelector(`[data-rn-base-input="${cssEscape(base)}"]`) : null;
    (input || details)?.scrollIntoView({ block: "center", behavior: "smooth" });
    input?.focus();
  }

  function onChange(ev) {
    const target = ev.target;
    if (!(target instanceof HTMLInputElement)) return;
    const row = rowOf(target);
    if (!row || locked()) return;
    if (target.hasAttribute("data-rn-select")) {
      if (!canSelect(row)) return;
      store.selected.set(row.key, target.checked);
    } else if (target.hasAttribute("data-rn-dup")) {
      if (row.status !== "duplicate" || !canRemoveDuplicates(store)) return;
      if (target.checked) store.removeDup.add(row.key);
      else store.removeDup.delete(row.key);
      // Struck through while it is to be removed.
      target.closest("[data-rn-key]")?.toggleAttribute("data-removing", target.checked);
    } else {
      return;
    }
    saveSelection();
    // The Apply count and the Select all/none toggles follow the selection.
    updateSelectionUi();
  }

  /* A preview that does not load (a removed model's dead URL) becomes the empty tile. */
  function onMediaError(ev) {
    const media = ev.target;
    if (!(media instanceof HTMLImageElement || media instanceof HTMLVideoElement)) return;
    const thumb = media.closest(".cu-thumb");
    if (!thumb || !container.contains(thumb)) return;
    thumb.removeAttribute("data-rn-action");
    media.replaceWith(Object.assign(document.createElement("div"), { className: "cu-thumb-empty", textContent: "No preview" }));
  }

  function onInput(ev) {
    const target = ev.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.dataset.rnDraft) {
      const row = rowOf(target);
      if (!row) return;
      const draft = store.drafts.get(row.key) || { ...row.parts };
      draft[target.dataset.rnDraft] = target.value;
      store.drafts.set(row.key, draft);
    } else if (target.dataset.rnBaseInput !== undefined) {
      const base = target.dataset.rnBaseInput;
      store.baseDrafts.set(base, target.value);
      const button = target.parentElement?.querySelector("[data-rn-action=\"save-base\"]");
      if (button) button.disabled = store.busy;
    }
  }

  function onKey(ev) {
    if (ev.key !== "Enter" && ev.key !== "Escape") return;
    const target = ev.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.dataset.rnDraft) {
      ev.preventDefault();
      const row = rowOf(target);
      if (ev.key === "Enter") saveEdit(row);
      else { store.editing = null; if (row) store.drafts.delete(row.key); render(); }
    } else if (target.dataset.rnBaseInput !== undefined && ev.key === "Enter") {
      ev.preventDefault();
      saveBaseCode(target.dataset.rnBaseInput);
    }
  }

  render();
  return { refresh, onJob, render };
}

function cssEscape(text) {
  if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(text);
  return String(text).replace(/["\\]/g, "\\$&");
}

/* ---------------------------------------------------------------- styles */

function injectRenameStyles() {
  if (typeof document === "undefined" || document.getElementById("cu-rn-styles")) return;
  const style = document.createElement("style");
  style.id = "cu-rn-styles";
  style.textContent = RENAME_CSS;
  document.head.appendChild(style);
}

const RENAME_CSS = `
  .cu-rn {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .cu-rn-hint {
    color: var(--cu-dust);
    font-size: 10.5px;
  }

  .cu-rn-mono {
    font-family: var(--cu-mono);
    font-size: 10.5px;
    overflow-wrap: anywhere;
  }

  .cu-rn-runbar {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .cu-rn-run {
    font-family: var(--cu-mono);
    font-size: 10.5px;
    color: var(--cu-ash);
    overflow-wrap: anywhere;
  }

  .cu-rn-banner {
    display: flex;
    flex-direction: column;
    gap: 7px;
    border: 1px solid var(--cu-seam-strong);
    border-radius: 5px;
    background: var(--cu-ink);
    padding: 8px 10px;
  }

  .cu-rn-banner[data-level="warn"] {
    border-color: rgba(210, 162, 76, 0.4);
    background: rgba(210, 162, 76, 0.06);
  }

  .cu-rn-banner[data-level="warn"] .cu-rn-banner-text {
    color: var(--cu-amber);
    font-weight: 600;
  }

  .cu-rn-banner-text {
    color: var(--cu-bone);
    font-size: 11px;
    overflow-wrap: anywhere;
  }

  .cu-rn-message {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 8px;
    font-size: 11px;
    color: var(--cu-ash);
    padding: 6px 9px;
    border-left: 2px solid var(--cu-seam-strong);
    overflow-wrap: anywhere;
  }

  .cu-rn-message[data-level="error"] {
    border-left-color: var(--cu-amber);
    color: var(--cu-amber);
  }

  .cu-rn-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    padding-top: 8px;
    border-top: 1px solid var(--cu-seam);
  }

  .cu-rn-counts {
    font-family: var(--cu-mono);
    font-size: 10.5px;
    color: var(--cu-ash);
  }

  .cu-rn-counts b {
    color: var(--cu-amber);
    font-weight: 500;
  }

  .cu-rn-result {
    border: 1px solid var(--cu-seam);
    border-radius: 5px;
    background: var(--cu-ink);
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    align-items: flex-start;
  }

  .cu-rn-result .cu-label {
    margin: 4px 0 0;
  }

  .cu-rn-result-head {
    font-family: var(--cu-mono);
    font-size: 10.5px;
    color: var(--cu-bone);
  }

  .cu-rn-list {
    margin: 0;
    padding-left: 16px;
    color: var(--cu-ash);
    font-size: 11px;
    overflow-wrap: anywhere;
  }

  .cu-rn-section {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .cu-rn-section-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    padding: 4px 0 2px;
    border-bottom: 1px solid var(--cu-seam);
  }

  .cu-rn-section-title,
  .cu-rn-section-toggle,
  .cu-rn-bases > summary {
    font-family: var(--cu-mono);
    font-size: 9.5px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--cu-ash);
  }

  .cu-rn-section-toggle {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: none;
    border: 0;
    padding: 0;
    cursor: pointer;
  }

  .cu-rn-section-toggle:hover {
    color: var(--cu-bone);
  }

  .cu-rn-section-count {
    color: var(--cu-dust);
    letter-spacing: 0.04em;
    margin-left: 4px;
  }

  /* ---- Cards: the Updates card (.cu-item, .cu-thumb, .cu-versions) with a
     checkbox in front of each file line. ---- */

  .cu-rn-cards {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  /* Thousands of cards: the browser skips laying out the ones off screen. */
  .cu-rn .cu-rn-card {
    content-visibility: auto;
    contain-intrinsic-size: auto 96px;
  }

  .cu-rn .cu-thumb:not([data-rn-action]) {
    cursor: default;
  }

  .cu-rn .cu-thumb:not([data-rn-action])::after {
    content: none;
  }

  .cu-rn-file {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .cu-rn .cu-versions .cu-rn-line {
    grid-template-columns: 14px 80px 60px minmax(0, 1fr);
    align-items: start;
  }

  /* The rail runs through the label dots, which sit after the checkbox. */
  .cu-rn .cu-versions::before {
    left: 23px;
  }

  .cu-rn .cu-rn-line > .cu-ver-label,
  .cu-rn .cu-rn-line > .cu-ver-base,
  .cu-rn-check {
    min-height: 17px;
    line-height: 17px;
  }

  .cu-rn-check {
    display: flex;
    align-items: center;
  }

  .cu-rn-check input {
    margin: 0;
  }

  .cu-rn .cu-rn-line .cu-ver-main {
    flex-wrap: wrap;
    align-items: flex-start;
    row-gap: 3px;
  }

  .cu-rn-names {
    display: flex;
    flex-direction: column;
    gap: 1px;
    min-width: 0;
    flex: 1 1 160px;
    line-height: 17px;
  }

  .cu-rn-names .cu-ver-link {
    flex: 0 1 auto;
    align-self: flex-start;
    max-width: 100%;
  }

  .cu-rn-new {
    color: var(--cu-bone);
    overflow-wrap: anywhere;
  }

  .cu-rn-arrow {
    color: var(--cu-dust);
    margin-right: 5px;
  }

  .cu-rn-diff {
    background: rgba(255, 255, 255, 0.1);
    color: var(--cu-bone);
    border-radius: 2px;
    padding: 0 1px;
  }

  .cu-rn .cu-rn-line .cu-inline-btn {
    margin-top: 1px;
  }

  .cu-rn-line-actions {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 5px;
  }

  /* With a mouse, a line's buttons show while it is hovered or focused, or
     always when the line needs a look. Touch screens always show them. */
  @media (hover: hover) {
    .cu-rn-file:not([data-attention]) .cu-rn-line-actions {
      visibility: hidden;
    }

    .cu-rn-file:hover .cu-rn-line-actions,
    .cu-rn-file:focus-within .cu-rn-line-actions {
      visibility: visible;
    }
  }

  /* Unchanged and kept files: quiet. */
  .cu-rn-file.is-quiet .cu-copy-path {
    color: var(--cu-dust);
  }

  /* An extra copy ticked for removal is struck through. */
  .cu-rn-file[data-removing] .cu-rn-old {
    color: var(--cu-dust);
    text-decoration: line-through;
  }

  .cu-rn .cu-ver-label[data-role="extra"] {
    color: var(--cu-amber);
  }

  .cu-rn .cu-ver-label[data-role="extra"]::before {
    border-style: dashed;
    border-color: var(--cu-amber);
  }

  /* Messages sit under their line, after the checkbox and the label. */
  .cu-rn .cu-rn-file .cu-issue-msg {
    margin: 0 0 1px 106px;
  }

  .cu-rn .cu-issue-msg[data-level="warn"],
  .cu-rn .cu-issue-msg[data-level="block"] {
    color: var(--cu-amber);
  }

  .cu-rn .cu-issue-msg[data-level="block"] {
    font-weight: 600;
  }

  .cu-rn .cu-issue-msg > button {
    margin-left: 4px;
  }

  .cu-rn .cu-rn-file .cu-rn-edit {
    margin-left: 20px;
  }

  @container (max-width: 430px) {
    .cu-rn .cu-versions .cu-rn-line {
      grid-template-columns: 14px 80px minmax(0, 1fr);
    }
  }

  @container (max-width: 330px) {
    .cu-rn .cu-rn-file .cu-issue-msg {
      margin-left: 20px;
    }
  }

  .cu-rn-chip {
    display: inline-flex;
    align-items: center;
    font-family: var(--cu-mono);
    font-size: 8.5px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--cu-ash);
    border: 1px solid var(--cu-seam-strong);
    border-radius: 3px;
    padding: 1px 5px;
    line-height: 1.3;
  }

  .cu-rn-flag {
    font-size: 11px;
    color: var(--cu-amber);
    font-weight: 600;
    overflow-wrap: anywhere;
  }

  .cu-rn-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 5px;
    margin-top: 2px;
  }

  .cu-rn .cu-inline-btn:disabled,
  .cu-rn .cu-text-btn:disabled {
    opacity: 0.35;
    cursor: default;
    pointer-events: none;
  }

  .cu-rn-edit {
    display: flex;
    flex-direction: column;
    gap: 5px;
    margin-top: 4px;
    padding: 7px;
    border: 1px solid var(--cu-seam);
    border-radius: 4px;
    background: var(--cu-ink);
  }

  .cu-rn-field {
    display: grid;
    grid-template-columns: 56px 1fr;
    align-items: center;
    column-gap: 6px;
    font-size: 10.5px;
    color: var(--cu-ash);
  }

  .cu-rn-field em {
    grid-column: 2;
    font-style: normal;
    font-size: 9.5px;
    color: var(--cu-dust);
  }

  .cu-rn input[type="text"] {
    box-sizing: border-box;
    width: 100%;
    min-width: 0;
    height: 24px;
    padding: 0 7px;
    border: 1px solid var(--cu-seam-strong);
    border-radius: 4px;
    background: var(--cu-raise);
    color: var(--cu-bone);
    font-family: var(--cu-mono);
    font-size: 10.5px;
  }

  .cu-rn input[type="text"]:focus {
    outline: none;
    border-color: var(--cu-dust);
  }

  .cu-rn-bases {
    border-top: 1px solid var(--cu-seam);
    padding-top: 6px;
  }

  .cu-rn-bases > summary {
    cursor: pointer;
    padding: 2px 0 6px;
    user-select: none;
  }

  .cu-rn-bases > summary:hover {
    color: var(--cu-bone);
  }

  .cu-rn-bases > .cu-rn-hint {
    margin-bottom: 6px;
  }

  .cu-rn-base {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto 90px auto;
    align-items: center;
    gap: 6px;
    padding: 3px 0;
  }

  .cu-rn-base-name {
    font-size: 11px;
    color: var(--cu-bone);
    overflow-wrap: anywhere;
  }

  .cu-rn-nocheck {
    display: inline-block;
    width: 13px;
  }
`;
