// Download and Replace on the Updates cards. Pure: nothing here touches ComfyUI or the page.

export const MODEL_DOWNLOAD_JOB = "model-download";
export const CONFIRM_FOR = 4000;
export const DONE_FOR = 4000;

export const COPY = {
  download: "Download",
  replace: "Replace",
  cancel: "Cancel",
  stop: "Stop",
  waiting: "Waiting",
  downloading: "Downloading",
  downloaded: "Downloaded",
  earlyAccess: "Early access",
  downloadTitle: "Download this release",
  delete: "Delete",
  deleteAsk: "Delete?",
  deleteConfirmTitle: "Click again to send it to the Recycle Bin",
  retry: "Retry",
  hide: "Hide",
  unhide: "Unhide",
  deleting: "Deleting…",
  deleteTitle: "Send this LoRA and its sidecars to the Recycle Bin",
  deleteTitleFor: (noun) => `Send this ${noun} and its sidecars to the Recycle Bin`,
  replaceTitle: "Download this release, then send the version you have to the Recycle Bin",
};

export const versionKey = (versionId) => `version:${versionId}`;
export const galleryKey = (name) => `gallery:${name}`;

export function latestRecords(requests) {
  const map = new Map();
  for (const record of requests || []) map.set(record.key, record);
  return map;
}

export function isPaid(version) {
  return (
    version?.paid === "early" ||
    version?.paid === "permanent" ||
    version?.availability === "EarlyAccess"
  );
}

export function percent(record) {
  const total = Number(record?.total) || 0;
  if (total <= 0) return null;
  const value = Math.floor(((Number(record.written) || 0) / total) * 100);
  return Math.max(0, Math.min(100, value));
}

export function slotState(record) {
  const state = record?.state;
  if (state === "waiting" || state === "downloading" || state === "done" || state === "error") {
    return state;
  }
  return "idle";
}

/* The job a waiting download is held behind, as a short phrase. */
export const WAITING_FOR = {
  "check-updates": "the check",
  scan: "the metadata fetch",
  "rename-dry-run": "the dry run",
  "rename-apply": "the rename",
  "naming-model-download": "the naming model download",
};

const waitingText = (record) =>
  record.waitingFor ? `Waiting for ${WAITING_FOR[record.waitingFor] ?? "another job"}` : "";

export function recordMessage(record) {
  if (!record) return "";
  if (record.error) return String(record.error);
  // A waiting card says so in its slot (or its overlay), not in a message line.
  if (record.state === "waiting" && record.waitingFor) return "";
  return (record.warnings || []).join(" ");
}

/* What a version row's bar, failure and warning text need from the download record.
   A finished download can carry warnings (a Replace whose old file stayed): they show
   in the row like a failure does, so they are never lost. */
export function rowDownloadView(record) {
  const state = slotState(record);
  return {
    state,
    pct: state === "downloading" ? percent(record) : null,
    failure: state === "error" ? recordMessage(record) : "",
    warning: state === "done" ? (record?.warnings || []).join(" ") : "",
  };
}

const note = (text) => `<span class="cu-dl-note">${text}</span>`;

/* An icon button: a fixed square holding a PrimeIcons glyph. */
const iconButton = (icon, label, attrs = "", extraClass = "") =>
  `<button type="button" class="cu-icon-btn${extraClass}"${attrs} title="${label}" aria-label="${label}"><i class="pi ${icon}" aria-hidden="true"></i></button>`;

const icon = (name, label, extraClass = "") =>
  `<span class="cu-dl-icon${extraClass}" title="${label}"><i class="pi ${name}" aria-hidden="true"></i></span>`;

/* Everything sits in one slot element, which the page gives one fixed width: at most two
   icon buttons, or a percentage and one button, in every state. */
export function versionActionsHtml(slot, record, escapeHtml) {
  const wrap = (inner, state) => `<span class="cu-slot" data-state="${state}">${inner}</span>`;
  if (slot?.paid) return wrap(note(COPY.earlyAccess), "paid");
  const state = slotState(record);
  const stop = (label, glyph) =>
    iconButton(glyph, label, ` data-dl-action="stop" data-request-id="${escapeHtml(record.id)}"`);
  // The slot says what it waits for, so the row needs no second line.
  if (state === "waiting") {
    const reason = escapeHtml(waitingText(record) || COPY.waiting);
    return wrap(icon("pi-clock", reason) + stop(COPY.cancel, "pi-times"), state);
  }
  if (state === "downloading") {
    const pct = percent(record);
    const text = pct === null ? "\u2026" : `${pct}%`;
    return wrap(
      `<span class="cu-dl-pct">${text}</span>` + stop(COPY.stop, "pi-stop-circle"),
      state
    );
  }
  if (state === "done") {
    const warning = (record?.warnings || []).join(" ");
    return warning
      ? wrap(icon("pi-exclamation-triangle", escapeHtml(warning), " is-warn"), state)
      : wrap(icon("pi-check", COPY.downloaded, " is-done"), state);
  }
  if (state === "error") {
    const action = slot?.canReplace && record?.replace ? "replace" : "download";
    const title = action === "replace" ? COPY.replaceTitle : COPY.retry;
    return wrap(
      iconButton("pi-refresh", title, ` data-dl-action="${action}"`).replace(
        `aria-label="${title}"`,
        `aria-label="${COPY.retry}"`
      ),
      state
    );
  }
  let html = iconButton("pi-download", COPY.downloadTitle, ' data-dl-action="download"');
  if (slot?.canReplace) {
    html += iconButton("pi-sync", COPY.replaceTitle, ' data-dl-action="replace"');
  }
  return wrap(html, state);
}

/* Hide or Unhide a new version: an icon button beside the slot. */
export function rowHideHtml(hidden, modelId, versionId, escapeHtml) {
  const attrs = (action) =>
    ` data-archive-action="${action}" data-model-id="${escapeHtml(modelId || "")}" data-version-id="${escapeHtml(versionId || "")}"`;
  return hidden
    ? iconButton("pi-eye", COPY.unhide, attrs("restore"))
    : iconButton("pi-eye-slash", COPY.hide, attrs("archive"));
}

export function finishedText(summary) {
  let text = `Done — ${Number(summary?.downloaded) || 0} downloaded`;
  if (summary?.failed) text += `, ${summary.failed} failed`;
  if (summary?.stopped) text += `, ${summary.stopped} stopped`;
  return text;
}

const NOUNS = {
  loras: "LoRA",
  checkpoints: "checkpoint",
  diffusion_models: "diffusion model",
  embeddings: "embedding",
};

/* The Gallery card's action slot. card = { missing, kind }, ui = { canTrash, confirming, deleting }. */
export function galleryActionHtml(card, record, ui, escapeHtml) {
  const button = (action, label, extra = "") =>
    `<button type="button" class="cu-inline-btn cu-g-btn" data-g-action="${action}"${extra}>${label}</button>`;
  if (card?.missing) {
    const state = slotState(record);
    if (state === "waiting" || state === "downloading") {
      const label = state === "waiting" ? COPY.cancel : COPY.stop;
      return button("stop", label, ` data-request-id="${escapeHtml(record.id)}"`);
    }
    if (state === "done") return "";
    return button("download", COPY.download, ` title="${COPY.downloadTitle}"`);
  }
  if (!ui?.canTrash) return "";
  const noun = NOUNS[card?.kind];
  const title = ` title="${noun ? COPY.deleteTitleFor(noun) : COPY.deleteTitle}"`;
  const mark = `class="cu-inline-btn cu-g-btn cu-g-del"`;
  if (ui.deleting) {
    return `<button type="button" ${mark} data-g-action="delete" data-deleting disabled${title}>${COPY.deleting}</button>`;
  }
  if (ui.confirming) {
    return `<button type="button" ${mark} data-g-action="delete" data-confirm${title}>${COPY.deleteAsk}</button>`;
  }
  return `<button type="button" ${mark} data-g-action="delete"${title}>${COPY.delete}</button>`;
}

/*
 * Delete on a version row of an Updates or Issues card, ui = { canTrash, confirming, deleting }.
 * Only for weights on this drive: never an archived row (the archive is never changed)
 * or an info file alone. The server deletes only a file the check lists.
 */
export function rowDeleteHtml(v, ui, escapeHtml) {
  if (!ui?.canTrash || v?.archived || v?.metadataOnly || !v?.modelPath) return "";
  const path = ` data-row-action="delete" data-path="${escapeHtml(v.modelPath)}"`;
  const mark = " cu-ver-del";
  if (ui.deleting) {
    return iconButton("pi-spin pi-spinner", COPY.deleting, `${path} data-deleting disabled`, mark);
  }
  if (ui.confirming) {
    return iconButton("pi-trash", COPY.deleteConfirmTitle, `${path} data-confirm`, mark);
  }
  return iconButton("pi-trash", COPY.deleteTitleFor("version"), path, mark);
}

/* The Gallery card's state overlay: Waiting, the percentage, the error; Downloaded only just after. */
export function galleryStateText(record, { justDone = false } = {}) {
  const state = slotState(record);
  if (state === "waiting") return waitingText(record) || COPY.waiting;
  if (state === "downloading") {
    const pct = percent(record);
    return pct === null ? COPY.downloading : `${pct}%`;
  }
  if (state === "done") return justDone ? COPY.downloaded : "";
  if (state === "error") return recordMessage(record);
  return "";
}
