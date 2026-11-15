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
  "kept-by-you": "Kept by you",
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

/* One flag code in plain words: {text, level, action?}. level is "block"
   (the name cannot be applied as it is), "warn" (check it) or "info". */
export function flagInfo(flag, row) {
  const [kind, arg] = flagParts(flag);
  const parts = (row && row.parts) || {};
  const value = arg ? String(parts[arg] ?? "") : "";
  const partWord = PART_WORDS[arg] || arg;
  switch (kind) {
    case "invented":
      if (/^v\d/i.test(arg)) return { level: "warn", text: `"${arg}" is not in Civitai's version name` };
      return { level: "warn", text: `"${arg}" is not in Civitai's title, version names or file names` };
    case "unparsed":
      return { level: "warn", text: "The naming model's answer could not be read, so this name is a fallback. Edit it or ask again." };
    case "empty":
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
    case "maker-from-description":
      return { level: "info", text: `The maker "${parts.creator || ""}" comes from the model's description, not the uploader`, action: "use-maker" };
    case "maker-not-in-description":
      return { level: "warn", text: `"${parts.creator || ""}" is not named in the description; check who made it` };
    case "new-base-code":
      return { level: "info", text: `New base code "${parts.base || ""}"${row?.baseModel ? ` for ${row.baseModel}` : ""}`, action: "base-link" };
    case "official-file":
      return { level: "warn", text: "Official file: ComfyUI templates may look for the original name" };
    case "file-not-in-release":
      return { level: "warn", text: "This file is not in the Civitai release (a conversion or a re-upload?)" };
    case "collision":
      return { level: "block", text: "Another file in this folder would get the same name" };
    case "exists":
      return { level: "block", text: "A file with the new name is already in the folder" };
    case "ask-again":
      return { level: "info", text: "The naming model names this model again on the next dry run" };
    default:
      return { level: "warn", text: String(flag || "") };
  }
}

export function flagMessage(flag, row) {
  return flagInfo(flag, row).text;
}

/* Why a kept, duplicate or error row is not renamed, in words. */
export function reasonText(row, byKey) {
  if (!row) return "";
  if (row.status === "duplicate") {
    const survivor = byKey && row.survivorKey ? byKey.get(row.survivorKey) : null;
    const name = survivor ? survivor.oldStem : fileName(row.survivorKey || "");
    return name ? `Extra copy of "${name}"` : "Extra copy of another file";
  }
  if (row.status === "error") return row.reason ? `Error: ${row.reason}` : "Error";
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

export function groupRows(rows) {
  const sections = { look: [], change: [], unchanged: [], kept: [] };
  for (const row of rows || []) sections[sectionOf(row)].push(row);
  // Errors and duplicates first: they need a decision before anything else.
  const rank = (row) => (row.status === "error" ? 0 : row.status === "duplicate" ? 1 : 2);
  sections.look.sort((a, b) => rank(a) - rank(b));
  return sections;
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

/* ---------------------------------------------------------------- the view */

/* deps: { store, getJson, postJson, escapeHtml, startJob(endpoint, type, payload, statusText),
   activeJob() -> {jobId, type, status} | null, setStatus(text), adoptNamingModel(name),
   setNamingModels(list) } */
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
      store.summary = data.summary || null;
      store.errors = Array.isArray(data.errors) ? data.errors : [];
      store.runStatus = data.status || "";
      store.applied = Boolean(data.applied);
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
    store.jobId = null;
    store.loadedJobId = null;
    setRows(store, []);
    store.summary = null;
    store.applied = false;
    writeLastJob(null);
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
    } else if (store.jobId && store.loadedJobId !== store.jobId) {
      await loadRows();
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
      store.result = null;
      store.recoverResult = null;
      store.editing = null;
      store.confirming = false;
      store.drafts.clear();
      store.limits = {};
      setRows(store, []);
      store.removeDup.clear();
      writeLastJob(data.jobId);
      notify(null);
    });
  }

  async function decide(payload, done) {
    if (locked()) return;
    await request(async () => {
      const data = await deps.postJson(`${ROUTE}/rename/decision`, payload);
      replaceRows(store, data.rows);
      if (done) done();
      notify(null);
    });
  }

  async function applySelected() {
    if (locked()) return;
    const payload = applyPayload(store);
    if (!payload.keys.length && !payload.removeDuplicates.length) return;
    await request(async () => {
      await deps.startJob(`${ROUTE}/jobs/rename-apply`, APPLY_JOB, payload, "Applying the renames\u2026");
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
      store.recoverResult = { action, done: data.done, skipped: Array.isArray(data.skipped) ? data.skipped : [] };
      if (store.status) store.status.journal = data.journal || { pending: false, steps: 0 };
      await loadStatus();
      // Finish moved files: the Updates view's results now carry the new paths.
      await deps.reloadResults?.();
    });
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
        const look = groupRows(store.rows).look.length;
        notify(`Dry run done: ${plural(store.rows.length, "entry", "entries")}, ${count(look)} need a look.`);
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

  function render() {
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
      html.push(headHtml());
      if (store.applied) {
        html.push(`<div class="cu-rn-banner" data-level="info"><div class="cu-rn-banner-text">These names were applied. Run a new dry run to see your files as they are now.</div></div>`);
      }
      if (store.confirming) html.push(confirmHtml());
      const sections = groupRows(store.rows);
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
    if (!store.message) return "";
    return `<div class="cu-rn-message" data-level="${store.message.level === "error" ? "error" : "info"}">${esc(store.message.text)}
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
    const sections = groupRows(store.rows);
    const changes = store.rows.filter((row) => row.status === "change").length;
    const keys = selectedKeys(store);
    const removals = duplicateKeys(store);
    const n = keys.length + removals.length;
    return `<div class="cu-rn-head">
      <div class="cu-rn-counts">Rename \u00b7 ${plural(store.rows.length, "entry", "entries")} \u00b7 ${count(changes)} change \u00b7 <b>${count(sections.look.length)} need a look</b></div>
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

  function sectionHtml(id, title, rows, open) {
    if (!rows.length) return "";
    const limit = store.limits[id] || PAGE;
    const shown = rows.slice(0, limit);
    const selectable = rows.filter(canSelect);
    const allOn = selectable.length && selectable.every((row) => store.selected.get(row.key));
    const toggle = (id === "change" || id === "look") && selectable.length
      ? `<button class="cu-text-btn" data-rn-action="select-all" data-rn-section="${id}" data-rn-on="${allOn ? "0" : "1"}"${disabledIf(locked())}>${allOn ? "Select none" : "Select all"}</button>`
      : "";
    const hideable = id === "unchanged" || id === "kept";
    const body = open
      ? `<div class="cu-rn-rows">${shown.map(rowHtml).join("")}</div>
        ${rows.length > limit ? `<button class="cu-text-btn cu-rn-more" data-rn-action="more" data-rn-section="${id}">Show ${count(Math.min(PAGE, rows.length - limit))} more of ${count(rows.length - limit)}</button>` : ""}`
      : "";
    return `<section class="cu-rn-section" data-rn-id="${id}">
      <div class="cu-rn-section-head">
        ${hideable
          ? `<button class="cu-rn-section-toggle" data-rn-action="toggle-section" data-rn-section="${id}" aria-expanded="${open}"><span class="cu-group-caret">${open ? "\u25be" : "\u25b8"}</span>${esc(title)} <span class="cu-rn-section-count">${count(rows.length)}${open ? "" : " \u00b7 hidden"}</span></button>`
          : `<span class="cu-rn-section-title">${esc(title)} <span class="cu-rn-section-count">${count(rows.length)}</span></span>`}
        ${open ? toggle : ""}
      </div>
      ${body}
    </section>`;
  }

  function rowHtml(row) {
    const key = esc(row.key);
    const isLocked = locked();
    const named = NAMED.has(row.status);
    const selectable = canSelect(row);
    const checked = selectable && store.selected.get(row.key);
    const checkbox = row.status === "change"
      ? `<input type="checkbox" data-rn-select${checked ? " checked" : ""}${disabledIf(!selectable || isLocked)} title="${selectable ? "Rename this file on Apply" : "Fix the problems below first"}">`
      : `<span class="cu-rn-nocheck"></span>`;
    const names = row.status === "change"
      ? `<div class="cu-rn-old" title="${esc(row.modelPath)}">${esc(row.oldStem)}</div><div class="cu-rn-new"><span class="cu-rn-arrow">\u2192</span>${esc(row.newStem)}</div>`
      : `<div class="cu-rn-same" title="${esc(row.modelPath)}">${esc(row.oldStem)}</div>`;
    const chips = row.status === "change" && (row.changed || []).length
      ? `<div class="cu-rn-chips">${row.changed.map((part) => `<span class="cu-rn-chip">${esc(PART_WORDS[part] || part)}</span>`).join("")}</div>`
      : "";
    const flags = named ? (row.flags || []).map((flag) => flagHtml(flag, row, isLocked)).join("") : "";
    let reason = "";
    if (!named) {
      reason = `<div class="cu-rn-flag" data-level="${row.status === "error" ? "block" : "info"}">${esc(reasonText(row, store.byKey))}</div>`;
    }
    const actions = [];
    if (named) {
      actions.push(`<button class="cu-inline-btn" data-rn-action="edit"${disabledIf(isLocked)}>Edit</button>`);
      if (row.status === "change") actions.push(`<button class="cu-inline-btn" data-rn-action="keep"${disabledIf(isLocked)}>Keep this name</button>`);
      if (!(row.flags || []).includes("ask-again")) actions.push(`<button class="cu-inline-btn" data-rn-action="ask-again"${disabledIf(isLocked)} title="Forget the saved name; the naming model names this model again on the next dry run">Ask again</button>`);
    } else if (row.status === "kept" && row.reason === "kept-by-you") {
      actions.push(`<button class="cu-inline-btn" data-rn-action="unkeep"${disabledIf(isLocked)}>Undo keep</button>`);
    } else if (row.status === "duplicate") {
      const canRemove = canRemoveDuplicates(store);
      const on = canRemove && store.removeDup.has(row.key);
      actions.push(`<button class="cu-inline-btn${on ? " is-on" : ""}" data-rn-action="remove-dup" aria-pressed="${on}"${disabledIf(isLocked || !canRemove)}${canRemove ? "" : ` title="${esc(NO_SEND2TRASH)}"`}>${on ? "\u2713 Removing extra copy" : "Remove extra copy"}</button>`);
      if (on) actions.push(`<span class="cu-rn-hint">Goes to the Recycle Bin on Apply</span>`);
      else if (!canRemove) actions.push(`<span class="cu-rn-hint">${esc(NO_SEND2TRASH)}</span>`);
    }
    const edit = store.editing === row.key ? editHtml(row) : "";
    return `<div class="cu-rn-row" data-rn-key="${key}" data-status="${esc(row.status)}">
      <label class="cu-rn-check">${checkbox}</label>
      <div class="cu-rn-body">
        ${names}${chips}${flags}${reason}
        ${actions.length ? `<div class="cu-rn-actions">${actions.join("")}</div>` : ""}
        ${edit}
      </div>
    </div>`;
  }

  function flagHtml(flag, row, isLocked) {
    const info = flagInfo(flag, row);
    let action = "";
    if (info.action === "use-maker" && row.parts?.creator) {
      action = ` <button class="cu-inline-btn" data-rn-action="use-maker"${disabledIf(isLocked)}>Use ${esc(row.parts.creator)}</button>`;
    } else if (info.action === "base-link") {
      action = ` <button class="cu-text-btn" data-rn-action="base-link" data-rn-base="${esc(row.baseModel || "")}">Base codes</button>`;
    }
    const icon = info.level === "info" ? "\u24d8" : "\u26a0";
    return `<div class="cu-rn-flag" data-level="${info.level}"><span class="cu-rn-flag-icon">${icon}</span><span>${esc(info.text)}</span>${action}</div>`;
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
        const rows = groupRows(store.rows)[target.dataset.rnSection] || [];
        for (const r of rows) if (canSelect(r)) store.selected.set(r.key, on);
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
      case "remove-dup":
        if (row && !locked() && canRemoveDuplicates(store)) {
          if (store.removeDup.has(row.key)) store.removeDup.delete(row.key);
          else store.removeDup.add(row.key);
          render();
        }
        break;
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
    if (!(target instanceof HTMLInputElement) || !target.hasAttribute("data-rn-select")) return;
    const row = rowOf(target);
    if (!row || !canSelect(row) || locked()) return;
    store.selected.set(row.key, target.checked);
    // The Apply count and the Select all/none toggles follow the selection.
    render();
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

  .cu-rn-rows {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .cu-rn-row {
    display: grid;
    grid-template-columns: 16px 1fr;
    gap: 8px;
    border: 1px solid var(--cu-seam);
    border-radius: 5px;
    padding: 7px 8px;
    background: rgba(0, 0, 0, 0.18);
    transition: border-color 0.15s ease;
  }

  .cu-rn-row:hover {
    border-color: var(--cu-seam-strong);
  }

  .cu-rn-check {
    padding-top: 1px;
  }

  .cu-rn-body {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
  }

  .cu-rn-old,
  .cu-rn-new,
  .cu-rn-same {
    font-family: var(--cu-mono);
    font-size: 10.5px;
    overflow-wrap: anywhere;
  }

  .cu-rn-old {
    color: var(--cu-dust);
  }

  .cu-rn-new {
    color: var(--cu-bone);
  }

  .cu-rn-same {
    color: var(--cu-ash);
  }

  .cu-rn-arrow {
    color: var(--cu-dust);
    margin-right: 5px;
  }

  .cu-rn-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
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
    color: var(--cu-ash);
    overflow-wrap: anywhere;
  }

  .cu-rn-flag[data-level="warn"],
  .cu-rn-flag[data-level="block"] {
    color: var(--cu-amber);
  }

  .cu-rn-flag[data-level="block"] {
    font-weight: 600;
  }

  .cu-rn-flag-icon {
    font-size: 10px;
    margin-right: 5px;
  }

  .cu-rn-flag > button {
    margin-left: 6px;
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

  .cu-rn .cu-inline-btn.is-on {
    border-color: var(--cu-amber);
    color: var(--cu-amber);
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
