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
  if (record.state === "waiting" && record.waitingFor) return waitingText(record);
  return (record.warnings || []).join(" ");
}

const note = (text) => `<span class="cu-dl-note">${text}</span>`;

export function versionActionsHtml(slot, record, escapeHtml) {
  if (slot?.paid) return note(COPY.earlyAccess);
  const state = slotState(record);
  const stopButton = (label) =>
    `<button type="button" class="cu-inline-btn" data-dl-action="stop" data-request-id="${escapeHtml(record.id)}">${label}</button>`;
  if (state === "waiting") return note(COPY.waiting) + stopButton(COPY.cancel);
  if (state === "downloading") {
    const pct = percent(record);
    return note(pct === null ? COPY.downloading : `${pct}%`) + stopButton(COPY.stop);
  }
  if (state === "done") return note(COPY.downloaded);
  let html = `<button type="button" class="cu-inline-btn" data-dl-action="download" title="${COPY.downloadTitle}">${COPY.download}</button>`;
  if (slot?.canReplace) {
    html += `<button type="button" class="cu-inline-btn" data-dl-action="replace" title="${COPY.replaceTitle}">${COPY.replace}</button>`;
  }
  return html;
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
