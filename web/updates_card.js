// The Updates and Issues card: a Gallery-style picture on top and one row per
// version below, each row with two fixed action cells. Pure: the markup and the
// rules behind it, nothing here touches ComfyUI or the page. The panel draws the
// <article> and wires the clicks.

import { issueMessage, rowIssue, rowLabel } from "./check_results.js";
import { formatCount } from "./gallery_core.js";
import {
  COPY,
  downloadRing,
  iconButton,
  isPaid,
  percent,
  rowDownloadView,
  slotState,
  slotVersions,
  versionKey,
  waitingText,
} from "./model_actions.js";

export const PICTURE_WIDTH = 450;
export const THUMB_WIDTH = 64;

export const CARD_COPY = {
  notInterested: "Not interested",
  showAgain: "Show again",
  notDownloaded: "not downloaded",
  noPreview: "No preview",
  archive: "Archive",
  archiveTitle: "Move this version to the archive drive",
  unarchive: "Unarchive",
  unarchiveTitle: "Move this version back to this drive",
  deleteArchivedTitle: "Send this version and its sidecars to the archive drive's recycle bin",
  newTag: (n) => `${n} new`,
  openPreview: (t) => `Open the preview of ${t}`,
  openOnCivitai: (t) => `Open ${t} on Civitai`,
};

const SEP = " · ";
const isHttps = (url) => /^https:\/\//i.test(url || "");

/* A smaller picture from Civitai's image service; any other https picture as it is. */
export function sizedImage(url, width) {
  if (!isHttps(url)) return "";
  if (!/^https:\/\/image\.civitai\.com\//i.test(url)) return url;
  return url.replace(/\/(?:original=true|width=\d+)\//, `/width=${width}/`);
}

/* The archive drive as a short name: "Z:", the share's host, or the last folder. */
export function archiveDriveLabel(folder) {
  const text = String(folder || "").trim();
  if (!text) return "";
  const letter = /^([A-Za-z]:)/.exec(text);
  if (letter) return letter[1];
  const parts = text.split(/[\\/]+/).filter(Boolean);
  if (/^(?:\\\\|\/\/)/.test(text)) return parts[0] || "";
  return parts[parts.length - 1] || "";
}

const TYPE_LABELS = {
  checkpoint: "Checkpoint",
  lora: "LoRA",
  vae: "VAE",
  unet: "UNet",
  embedding: "Embedding",
};

export function typeLabel(modelType) {
  const text = String(modelType || "");
  return TYPE_LABELS[text.toLowerCase()] || text.charAt(0).toUpperCase() + text.slice(1);
}

/* The Buzz chip of a paid release: { kind, short, title }, or null for a free one. */
export function paidChip(version, { now = Date.now(), formatDate = (iso) => iso } = {}) {
  if (version?.paid === "permanent") {
    return {
      kind: "permanent",
      short: "Buzz · always",
      title: "Costs Buzz to download, with no free date.",
    };
  }
  if (version?.paid !== "early") return null;
  const ends = version.paidUntil ? new Date(version.paidUntil) : null;
  if (!ends || Number.isNaN(ends.getTime())) {
    return {
      kind: "early",
      short: "Buzz · early",
      title: "Early access: costs Buzz to download for now. Civitai gives no end date.",
    };
  }
  const days = Math.max(1, Math.ceil((ends.getTime() - Number(now)) / 86400000));
  return {
    kind: "early",
    short: `Buzz · ${days}d left`,
    title: `Early access: costs Buzz to download until ${formatDate(version.paidUntil)}, free after that.`,
  };
}

// --- rows ----------------------------------------------------------------------------

const fileName = (path) =>
  String(path || "")
    .split(/[\\/]/)
    .pop();
const fileStemOf = (path) => fileName(path).replace(/\.[^.]+$/, "");

function paidKind(version) {
  if (version?.paid === "permanent") return "permanent";
  return isPaid(version) ? "early" : "";
}

function releaseSlot(item, v, hidden) {
  const { reference } = slotVersions(item.localVersions);
  const kind = paidKind(v);
  return {
    kind: "release",
    dlKey: versionKey(v.versionId),
    modelId: String(item.modelId ?? ""),
    versionId: String(v.versionId ?? ""),
    path: reference?.modelPath || "",
    hidden,
    paid: Boolean(kind),
    paidKind: kind,
    paidUntil: v.paidUntil || "",
    place: "drive",
    canDelete: false,
    canMove: false,
  };
}

function fileSlot(item, v) {
  const none = { kind: "none", dlKey: "", place: "drive", canDelete: false, canMove: false };
  const base = {
    modelId: String(item.modelId ?? ""),
    versionId: String(v.versionId ?? ""),
    path: v.modelPath || "",
    hidden: false,
    paid: false,
    paidKind: "",
    paidUntil: "",
  };
  if (v.deleted) return { ...none, ...base };
  if (v.archived) {
    return {
      ...base,
      kind: "file",
      dlKey: v.moveKey || "",
      place: "archive",
      canDelete: true,
      canMove: true,
    };
  }
  // An info file alone on this drive: nothing to delete or move.
  if (v.metadataOnly) return { ...none, ...base };
  return {
    ...base,
    kind: "file",
    dlKey: v.moveKey || "",
    place: "drive",
    canDelete: true,
    canMove: Boolean(v.versionId),
  };
}

function releaseRow(item, v, hidden) {
  const name = v.rowName || v.versionName || "?";
  return {
    key: `v:${v.versionId}`,
    kind: hidden ? "hidden" : "new",
    role: hidden ? "hidden" : "new",
    label: hidden ? CARD_COPY.notInterested : "New",
    name,
    nameTitle: v.fileStem || name,
    base: v.baseModel || "",
    date: v.versionDate || v.publishedAt || "",
    picture: {
      url: v.previewUrl || item.previewUrl || "",
      type: (v.previewUrl ? v.previewType : item.previewType) || "",
    },
    localPath: "",
    issue: false,
    version: v,
    slot: releaseSlot(item, v, hidden),
  };
}

function fileRow(item, v) {
  const path = v.filePath || v.modelPath || "";
  const { role, label } = rowLabel(v);
  const name = v.rowName || v.versionName || fileStemOf(path) || "?";
  return {
    key: `f:${String(v.modelPath || "").toLowerCase()}`,
    kind: v.deleted ? "deleted" : v.archived ? "archive" : "drive",
    role,
    label,
    name,
    nameTitle: v.fileName || name,
    base: v.baseModel || "",
    date: v.versionDate || v.publishedAt || "",
    picture: {
      url: v.previewUrl || item.previewUrl || "",
      type: (v.previewUrl ? v.previewType : item.previewType) || "",
    },
    localPath: v.hasLocalPreview && !v.archived ? v.modelPath || "" : "",
    issue: Boolean(rowIssue(v)),
    version: v,
    slot: fileSlot(item, v),
  };
}

/* One row per version of a card, newest first as the server orders them. Issues lists
   no releases, and a release the user is not interested in comes only with showHidden. */
export function cardRows(item, { mode = "updates", showHidden = false } = {}) {
  const issues = mode === "issues";
  const saved = item.localVersions || [];
  const fresh = issues ? [] : item.newVersions || [];
  const hidden = !issues && showHidden ? item.hiddenNewVersions || [] : [];
  const lists = { saved, new: fresh, hidden };
  // A server not yet restarted sends no order; its releases are all newer than its files.
  const order = (
    item.versionOrder || [
      ...fresh.map((_, i) => ["new", i]),
      ...hidden.map((_, i) => ["hidden", i]),
      ...saved.map((_, i) => ["saved", i]),
    ]
  ).filter(([kind, index]) => lists[kind]?.[index]);
  return order.map(([kind, index]) => {
    const v = lists[kind][index];
    return kind === "saved" ? fileRow(item, v) : releaseRow(item, v, kind === "hidden");
  });
}

/* The row a card shows when nothing is remembered. */
export function defaultRowKey(rows, mode) {
  if (!rows.length) return "";
  const wanted = mode === "issues" ? rows.find((r) => r.issue) : rows.find((r) => r.kind === "new");
  return (wanted || rows[0]).key;
}

export function pickRowKey(rows, remembered, mode) {
  return rows.some((r) => r.key === remembered) ? remembered : defaultRowKey(rows, mode);
}

/* What a row's second line says: where the version is. */
export function infoLine(
  row,
  { mode = "updates", drive = "", formatDate = (iso) => iso, now } = {}
) {
  if (mode === "issues" && row.issue) return issueMessage(row.version);
  const date = row.date ? formatDate(row.date) : "";
  let head = row.label;
  let tail = "";
  if (row.kind === "new") {
    const chip = row.slot.paid
      ? paidChip({ ...row.version, paid: row.slot.paidKind }, { now, formatDate })
      : null;
    tail = chip ? chip.short : CARD_COPY.notDownloaded;
  } else if (row.kind === "hidden") {
    tail = CARD_COPY.notDownloaded;
  } else if (row.kind === "archive") {
    head = "Archived";
    tail = drive ? `on ${drive}` : "";
  } else {
    tail = row.version.fileName || fileName(row.version.filePath || row.version.modelPath);
  }
  return [head, date, tail].filter(Boolean).join(SEP);
}

// --- the slot ------------------------------------------------------------------------

const cell = (inner = "") => `<span class="cu-u-cell">${inner}</span>`;

/* An icon button whose tooltip says more than its screen-reader name. */
const button = (icon, title, label, attrs = "", extraClass = "") =>
  iconButton(icon, title, attrs, extraClass).replace(
    `aria-label="${title}"`,
    `aria-label="${label}"`
  );

const glyph = (name, title, extraClass = "") =>
  `<span class="cu-dl-icon${extraClass}" title="${title}"><i class="pi ${name}" aria-hidden="true"></i></span>`;

/* The two cells of a version row's action slot: always two, so no row ever changes width. */
export function rowActionsHtml(slot, record, ui, escapeHtml) {
  const esc = escapeHtml;
  const two = (first, second) => cell(first) + cell(second);
  if (!slot || slot.kind === "none") return two("", "");
  const state = slotState(record);
  const path = ` data-path="${esc(slot.path || "")}"`;

  const hideCell = () => {
    const attrs = (action) =>
      ` data-hide-action="${action}" data-model-id="${esc(slot.modelId || "")}" data-version-id="${esc(slot.versionId || "")}"`;
    return slot.hidden
      ? iconButton("pi-eye", CARD_COPY.showAgain, attrs("show"))
      : iconButton("pi-eye-slash", CARD_COPY.notInterested, attrs("hide"));
  };
  const downloadCell = () => {
    if (slot.paid) {
      const chip = paidChip(
        { paid: slot.paidKind, paidUntil: slot.paidUntil },
        { now: ui?.now, formatDate: ui?.formatDate }
      );
      const title = esc(chip?.title || COPY.earlyAccess);
      return `<span class="cu-u-buzz" data-paid="${esc(slot.paidKind)}" role="img" title="${title}" aria-label="${title}"><i class="pi pi-bolt" aria-hidden="true"></i></span>`;
    }
    return button("pi-download", COPY.downloadTitle, COPY.download, ' data-dl-action="download"');
  };
  const deleteCell = () => {
    if (!ui?.canTrash || !slot.canDelete) return "";
    const own = ` data-row-action="delete"${path}`;
    if (ui.asking === "deleting") {
      return iconButton("pi-spin pi-spinner", COPY.deleting, `${own} data-deleting disabled`);
    }
    if (ui.asking === "confirming") {
      return iconButton("pi-trash", COPY.deleteConfirmTitle, `${own} data-confirm`);
    }
    const title = esc(
      slot.place === "archive" ? CARD_COPY.deleteArchivedTitle : COPY.deleteTitleFor("version")
    );
    return button("pi-trash", title, COPY.delete, own);
  };
  const moveCell = () => {
    if (!ui?.archiveSet || !ui?.canTrash || !slot.canMove) return "";
    return slot.place === "archive"
      ? button(
          "pi-undo",
          CARD_COPY.unarchiveTitle,
          CARD_COPY.unarchive,
          ` data-row-action="unarchive"${path}`
        )
      : button(
          "pi-box",
          CARD_COPY.archiveTitle,
          CARD_COPY.archive,
          ` data-row-action="archive"${path}`
        );
  };
  // The first cell and the second of a slot with nothing running.
  const resting = () =>
    slot.kind === "release" ? [downloadCell(), hideCell()] : [deleteCell(), moveCell()];

  if (state === "waiting") {
    const reason = esc(waitingText(record) || COPY.waiting);
    return two(
      glyph("pi-clock", reason),
      iconButton(
        "pi-times",
        COPY.cancel,
        ` data-dl-action="stop" data-request-id="${esc(record.id)}"`
      )
    );
  }
  if (state === "downloading") {
    return two(
      downloadRing(percent(record)),
      iconButton(
        "pi-stop-circle",
        COPY.stop,
        ` data-dl-action="stop" data-request-id="${esc(record.id)}"`
      )
    );
  }
  if (state === "done") {
    const warning = (record?.warnings || []).join(" ");
    return two(
      warning
        ? glyph("pi-exclamation-triangle", esc(warning), " is-warn")
        : glyph("pi-check", COPY.downloaded, " is-done"),
      ""
    );
  }
  if (state === "error") {
    const retry = (attrs) => button("pi-refresh", COPY.retry, COPY.retry, attrs);
    if (slot.kind === "release") {
      return two(retry(' data-dl-action="download"'), hideCell());
    }
    const action =
      record?.to === "drive" || (record?.to !== "archive" && slot.place === "archive")
        ? "unarchive"
        : "archive";
    return two(deleteCell(), retry(` data-row-action="${action}"${path}`));
  }
  const [first, second] = resting();
  return two(first, second);
}

// --- the card ------------------------------------------------------------------------

const pillText = (item, row) => {
  const type = typeLabel(item.modelType);
  return row?.base ? `${type} | ${row.base}` : type;
};

function pictureHtml(row, esc) {
  const { url, type } = row?.picture || {};
  if (!isHttps(url))
    return `<span class="cu-g-noimg" title="${CARD_COPY.noPreview}">${CARD_COPY.noPreview}</span>`;
  if (type === "video") {
    return `<video src="${esc(url)}#t=0.5" preload="metadata" muted playsinline></video>`;
  }
  return `<img src="${esc(sizedImage(url, PICTURE_WIDTH))}" alt="" loading="lazy" decoding="async">`;
}

function thumbHtml(row, esc) {
  const { url, type } = row.picture;
  if (!isHttps(url)) return `<span class="cu-u-tn"></span>`;
  if (type === "video") {
    return `<span class="cu-u-tn"><video src="${esc(url)}#t=0.5" preload="metadata" muted playsinline></video></span>`;
  }
  return `<span class="cu-u-tn"><img src="${esc(sizedImage(url, THUMB_WIDTH))}" alt="" loading="lazy" decoding="async"></span>`;
}

function statsHtml(stats, esc) {
  if (!stats) return { numbers: "", likes: `<span class="cu-g-likes"></span>` };
  const stat = (icon, noun, value) =>
    `<span class="cu-g-stat" title="${esc(`${value} ${noun}`)}"><i class="pi pi-${icon}" aria-hidden="true"></i>${esc(formatCount(value))}</span>`;
  return {
    numbers:
      stat("download", "downloads", stats.downloads) +
      stat("comment", "comments", stats.comments) +
      stat("bolt", "Buzz tipped", stats.tipped),
    likes: `<span class="cu-g-likes" title="${esc(`${stats.likes} likes`)}"><i class="pi pi-thumbs-up" aria-hidden="true"></i>${esc(formatCount(stats.likes))}</span>`,
  };
}

function rowHtml(item, row, ui, esc) {
  const record = ui.records?.get(row.slot.dlKey);
  const view = rowDownloadView(record);
  const filePath = row.version.filePath || row.version.modelPath || "";
  const copyable = row.kind === "drive" || row.kind === "archive";
  const name = copyable
    ? `<span class="cu-u-name cu-copy-path" data-path="${esc(filePath)}" title="${esc(ui.copyTitle ? ui.copyTitle(filePath) : filePath)}">${esc(row.name)}</span>`
    : `<span class="cu-u-name" title="${esc(row.nameTitle)}">${esc(row.name)}</span>`;
  const failure = view.failure || view.warning;
  const info = infoLine(row, ui);
  const selected = row.key === ui.selectedKey ? ' aria-current="true"' : "";
  const slot = esc(JSON.stringify(row.slot));
  return (
    `<div class="cu-u-row" role="listitem" tabindex="0" data-u-row="${esc(row.key)}"` +
    ` data-pic="${esc(row.picture.url)}" data-pic-type="${esc(row.picture.type)}"` +
    ` data-local-path="${esc(row.localPath)}" data-pill="${esc(pillText(item, row))}"` +
    ` data-info="${esc(info)}"${selected} data-dl-state="${view.state}" data-dl-warn="${view.warning ? 1 : 0}">` +
    thumbHtml(row, esc) +
    `<span class="cu-u-dot" data-role="${esc(row.role)}" title="${esc(row.label)}" aria-label="${esc(row.label)}"></span>` +
    `<span class="cu-u-main">${name}<span class="cu-u-fail"${failure ? ` title="${esc(failure)}"` : ""}>${esc(failure)}</span></span>` +
    `<span class="cu-dl cu-u-acts" data-dl-key="${esc(row.slot.dlKey)}" data-slot="${slot}">${rowActionsHtml(row.slot, record, ui, esc)}</span>` +
    `</div>`
  );
}

/* The inner HTML of an Updates or Issues card; the panel makes the <article>. */
export function updatesCardHtml(item, rows, ui, escapeHtml) {
  const esc = escapeHtml;
  const key = pickRowKey(rows, ui.selectedKey, ui.mode);
  const selected = rows.find((r) => r.key === key);
  const shown = { ...ui, selectedKey: key };
  const title =
    item.title ||
    item.modelName ||
    fileStemOf(item.localVersions?.[0]?.filePath || item.localVersions?.[0]?.modelPath) ||
    "?";
  const creator = item.creator || item.creatorName || "";
  const stats = item.modelStats;
  const avatar = isHttps(stats?.creatorImage)
    ? `<img class="cu-g-avatar" src="${esc(stats.creatorImage)}" alt="" loading="lazy" decoding="async">`
    : `<span class="cu-g-avatar">${esc(Array.from(creator)[0]?.toUpperCase() ?? "")}</span>`;
  const { numbers, likes } = statsHtml(stats, esc);
  const pill = pillText(item, selected);
  const info = selected ? infoLine(selected, shown) : "";
  const fresh = rows.filter((r) => r.kind === "new").length;
  const tag =
    fresh > 1
      ? `<span class="cu-u-new" title="${esc(CARD_COPY.newTag(fresh))}">${esc(CARD_COPY.newTag(fresh))}</span>`
      : "";
  const link = isHttps(item.modelUrl)
    ? `<a class="cu-g-open" href="${esc(item.modelUrl)}" target="_blank" rel="noopener noreferrer" title="${esc(CARD_COPY.openOnCivitai(title))}" aria-label="${esc(CARD_COPY.openOnCivitai(title))}">↗</a>`
    : "";
  return (
    `<div class="cu-u-top">` +
    `<button type="button" class="cu-u-pic" data-u-pic aria-label="${esc(CARD_COPY.openPreview(title))}">` +
    `<span class="cu-g-thumb">${pictureHtml(selected, esc)}</span>` +
    `<span class="cu-g-pills"><span class="cu-g-pill" data-u-pill title="${esc(pill)}">${esc(pill)}</span></span>` +
    tag +
    `<span class="cu-g-info">` +
    `<span class="cu-g-by">${avatar}<span class="cu-g-creator" title="${esc(creator)}">${esc(creator)}</span></span>` +
    `<span class="cu-g-title" title="${esc(title)}">${esc(title)}</span>` +
    `<span class="cu-u-info" data-u-info title="${esc(info)}">${esc(info)}</span>` +
    `<span class="cu-g-foot"><span class="cu-g-stats">${numbers}</span>${likes}</span>` +
    `</span></button>${link}</div>` +
    `<div class="cu-u-rows" role="list">${rows.map((row) => rowHtml(item, row, shown, esc)).join("")}</div>`
  );
}

// --- style ---------------------------------------------------------------------------

/* Appended to the panel's stylesheet. It overrides the shared .cu-item layout through
   .cu-item.cu-u-card and reuses the Gallery's .cu-g-* overlay rules for the picture. */
export const UPDATES_CARD_CSS = `
    .cu-results {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 6px;
      align-items: start;
    }

    .cu-results > .cu-group-head-wrap,
    .cu-results > .cu-mature-note,
    .cu-results > .cu-empty,
    .cu-results > .cu-loading-note {
      grid-column: 1 / -1;
    }

    .cu-item.cu-u-card {
      display: block;
      min-width: 0;
      padding: 0;
      overflow: hidden;
    }

    .cu-u-top {
      position: relative;
    }

    .cu-u-pic {
      position: relative;
      display: block;
      box-sizing: border-box;
      width: 100%;
      height: 168px;
      margin: 0;
      padding: 0;
      border: 0;
      background: var(--cu-raise);
      color: inherit;
      font: inherit;
      text-align: left;
      cursor: zoom-in;
      overflow: hidden;
    }

    .cu-u-pic video {
      position: absolute;
      inset: 0;
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .cu-u-top:hover .cu-g-open {
      opacity: 0.9;
    }

    /* The count of new releases, where the Gallery puts its version chip. */
    .cu-u-new {
      position: absolute;
      top: 34px;
      right: 6px;
      box-sizing: border-box;
      height: 22px;
      padding: 0 8px;
      border-radius: 11px;
      background: rgba(19, 19, 22, 0.72);
      color: var(--cu-red-soft);
      font-family: var(--cu-mono);
      font-size: 9.5px;
      line-height: 22px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .cu-u-info {
      display: block;
      height: 15px;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      font-family: var(--cu-mono);
      font-size: 9.5px;
      line-height: 15px;
      color: var(--cu-ash);
    }

    .cu-u-rows {
      display: flex;
      flex-direction: column;
      padding: 3px 0;
    }

    .cu-u-row {
      display: grid;
      grid-template-columns: 28px 7px minmax(0, 1fr) 52px;
      align-items: center;
      column-gap: 7px;
      box-sizing: border-box;
      height: 30px;
      padding: 0 8px;
      cursor: pointer;
    }

    .cu-u-row:hover,
    .cu-u-row:focus-visible,
    .cu-u-row[aria-current="true"] {
      background: var(--cu-raise);
    }

    .cu-u-tn {
      display: block;
      width: 28px;
      height: 22px;
      overflow: hidden;
      border-radius: 3px;
      background: var(--cu-raise);
      border: 1px solid var(--cu-seam);
      box-sizing: border-box;
    }

    .cu-u-tn img,
    .cu-u-tn video {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .cu-u-dot {
      width: 7px;
      height: 7px;
      box-sizing: border-box;
      border-radius: 50%;
      background: var(--cu-slate);
      border: 1.5px solid var(--cu-dust);
    }

    .cu-u-dot[data-role="saved"] {
      border-color: #b7b4bd;
    }

    .cu-u-dot[data-role="metadata"],
    .cu-u-dot[data-role="found"] {
      border-color: var(--cu-dust);
    }

    .cu-u-dot[data-role="downloaded"] {
      background: var(--cu-moss);
      border-color: var(--cu-moss);
    }

    .cu-u-dot[data-role="archived"],
    .cu-u-dot[data-role="nomatch"] {
      border-style: dashed;
      border-color: var(--cu-ash);
    }

    .cu-u-dot[data-role="duplicate"],
    .cu-u-dot[data-role="error"] {
      background: var(--cu-amber);
      border-color: var(--cu-amber);
    }

    .cu-u-dot[data-role="infoonly"] {
      border-style: dashed;
      border-color: var(--cu-amber);
    }

    .cu-u-dot[data-role="new"] {
      background: var(--cu-red);
      border-color: var(--cu-red);
      box-shadow: 0 0 6px rgba(255, 75, 62, 0.55);
    }

    .cu-u-dot[data-role="hidden"] {
      background: var(--cu-seam-strong);
      border-color: var(--cu-seam-strong);
    }

    .cu-u-dot[data-role="deleted"] {
      border-style: dashed;
      border-color: var(--cu-dust);
    }

    .cu-u-main {
      position: relative;
      min-width: 0;
      height: 22px;
    }

    .cu-u-name,
    .cu-u-fail {
      display: block;
      min-width: 0;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      font-size: 11px;
      line-height: 22px;
      color: var(--cu-bone);
    }

    .cu-u-fail {
      position: absolute;
      inset: 0;
      display: none;
      color: var(--cu-red-soft);
    }

    .cu-u-row[data-dl-state="error"] .cu-u-name,
    .cu-u-row[data-dl-warn="1"] .cu-u-name {
      visibility: hidden;
    }

    .cu-u-row[data-dl-state="error"] .cu-u-fail,
    .cu-u-row[data-dl-warn="1"] .cu-u-fail {
      display: block;
    }

    .cu-u-row[data-dl-warn="1"] .cu-u-fail {
      color: var(--cu-amber);
    }

    .cu-u-acts {
      display: flex;
      justify-content: flex-end;
      width: 52px;
    }

    .cu-u-cell {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 24px;
      width: 24px;
      height: 22px;
    }

    .cu-u-buzz {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      box-sizing: border-box;
      width: 24px;
      height: 22px;
      border: 1px dashed rgba(210, 162, 76, 0.5);
      border-radius: 4px;
      background: rgba(210, 162, 76, 0.07);
      color: var(--cu-amber);
      font-size: 11px;
    }

    .cu-u-buzz[data-paid="permanent"] {
      border-style: solid;
      background: rgba(210, 162, 76, 0.16);
    }

    .cu-item[data-mature="blur"] .cu-u-pic img,
    .cu-item[data-mature="blur"] .cu-u-pic video,
    .cu-item[data-mature="blur"] .cu-u-tn img,
    .cu-item[data-mature="blur"] .cu-u-tn video {
      filter: blur(10px) saturate(0.6);
      transform: scale(1.06);
    }
`;
