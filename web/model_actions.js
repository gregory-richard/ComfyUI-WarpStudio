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

export function recordMessage(record) {
  if (!record) return "";
  if (record.error) return String(record.error);
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
