// Downloads, moves and deletes on the Updates and Gallery cards. Pure: nothing here touches
// ComfyUI or the page.

export const MODEL_DOWNLOAD_JOB = "model-download";
export const CONFIRM_FOR = 4000;
export const DONE_FOR = 4000;

export const COPY = {
  download: "Download",
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
  starting: "Starting…",
  checking: "Checking the file…",
  deleting: "Deleting…",
  deleteTitle: "Send this LoRA and its sidecars to the Recycle Bin",
  deleteTitleFor: (noun) => `Send this ${noun} and its sidecars to the Recycle Bin`,
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

export const waitingText = (record) =>
  record.waitingFor ? `Waiting for ${WAITING_FOR[record.waitingFor] ?? "another job"}` : "";

export function recordMessage(record) {
  if (!record) return "";
  if (record.error) return String(record.error);
  // A waiting card says so in its slot (or its overlay), not in a message line.
  if (record.state === "waiting" && record.waitingFor) return "";
  return (record.warnings || []).join(" ");
}

/* What a version row's bar, failure and warning text need from the download record.
   A finished request can carry warnings (a move whose original stayed): they show
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

/* An icon button: a fixed square holding a PrimeIcons glyph. */
export const iconButton = (icon, label, attrs = "", extraClass = "") =>
  `<button type="button" class="cu-icon-btn${extraClass}"${attrs} title="${label}" aria-label="${label}"><i class="pi ${icon}" aria-hidden="true"></i></button>`;

/* A download in the button's place: a ring that fills, spinning while nothing is
   known yet (the request starts) and once every byte is in (the file is checked). */
const RING = 2 * Math.PI * 7;
export function downloadRing(pct) {
  const spin = pct === null || pct <= 0 || pct >= 100;
  const title = pct === null || pct <= 0 ? COPY.starting : pct >= 100 ? COPY.checking : `${pct}%`;
  const offset = spin ? RING * 0.7 : RING * (1 - pct / 100);
  const value = pct === null ? "" : ` aria-valuenow="${pct}"`;
  return (
    `<span class="cu-dl-ring${spin ? " is-spin" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="100"${value} title="${title}" aria-label="${title}">` +
    `<svg viewBox="0 0 20 20" aria-hidden="true"><circle class="cu-dl-ring-track" cx="10" cy="10" r="7"></circle>` +
    `<circle class="cu-dl-ring-fill" cx="10" cy="10" r="7" style="stroke-dashoffset: ${offset.toFixed(2)}"></circle></svg></span>`
  );
}

/* Which of a card's local versions a release row works from. ``reference``: the file whose
   folder the download goes beside (the server maps an archived one to its drive folder). */
export function slotVersions(localVersions) {
  const versions = localVersions || [];
  const reference =
    versions.find((v) => !v.deleted && !v.archived) ||
    versions.find((v) => !v.deleted) ||
    versions[0];
  return { reference };
}

export function finishedText(summary) {
  const downloaded = Number(summary?.downloaded) || 0;
  const moved = Number(summary?.moved) || 0;
  const parts = [];
  if (downloaded || !moved) parts.push(`${downloaded} downloaded`);
  if (moved) parts.push(`${moved} moved`);
  if (summary?.failed) parts.push(`${summary.failed} failed`);
  if (summary?.stopped) parts.push(`${summary.stopped} stopped`);
  return `Done — ${parts.join(", ")}`;
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
