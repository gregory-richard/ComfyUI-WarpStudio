import assert from "node:assert/strict";
import { test } from "node:test";

import { issueMessage } from "../../web/check_results.js";
import {
  CARD_COPY,
  PICTURE_WIDTH,
  THUMB_WIDTH,
  UPDATES_CARD_CSS,
  archiveDriveLabel,
  cardRows,
  defaultRowKey,
  infoLine,
  paidChip,
  pickRowKey,
  rowActionsHtml,
  sizedImage,
  typeLabel,
  updatesCardHtml,
} from "../../web/updates_card.js";

const escapeHtml = (text) =>
  String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const reEsc = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const fmt = (iso) => iso.slice(0, 10);
const NOW = Date.parse("2026-03-10T00:00:00Z");

const saved = "D:\\L\\moss - glow - v1 (ill).safetensors";
const archivedPath = "Z:\\L\\moss - glow - v0 (ill).safetensors";

function makeItem(extra = {}) {
  return {
    modelId: "55",
    modelType: "lora",
    title: "glow lite",
    creator: "moss",
    modelName: "MiniMax H3 Image",
    modelUrl: "https://civitai.red/models/55",
    modelStats: {
      downloads: 1200,
      comments: 3,
      tipped: 40,
      likes: 15,
      creatorImage: "https://image.civitai.com/a/b/width=96/me.jpeg",
    },
    previewUrl: "https://image.civitai.com/x/abc/original=true/9.jpeg",
    previewType: "image",
    newVersions: [
      {
        versionId: "202",
        versionName: "h3",
        rowName: "h3 multi char swap (mmh3i)",
        fileStem: "glow-h3",
        baseModel: "MiniMax H3 Image",
        versionDate: "2026-03-01T10:00:00Z",
        previewUrl: "https://image.civitai.com/x/abc/original=true/1.jpeg",
        previewType: "image",
      },
      {
        versionId: "203",
        versionName: "v4",
        rowName: "v4",
        baseModel: "Illustrious",
        versionDate: "2026-02-01T10:00:00Z",
        previewUrl: "https://image.civitai.com/x/abc/original=true/2.mp4",
        previewType: "video",
      },
    ],
    hiddenNewVersions: [
      {
        versionId: "204",
        versionName: "v5",
        rowName: "v5",
        baseModel: "Illustrious",
        versionDate: "2026-01-15T10:00:00Z",
      },
    ],
    localVersions: [
      {
        versionId: "101",
        versionName: "v1",
        rowName: "xxl (moss)",
        fileName: "moss - glow - v1 (ill).safetensors",
        filePath: saved,
        modelPath: saved,
        moveKey: "move:0123456789abcdef",
        baseModel: "Illustrious",
        publishedAt: "2026-01-01T10:00:00Z",
        hasLocalPreview: true,
        status: "ok",
      },
      {
        versionId: "100",
        versionName: "v0",
        rowName: "v1 (ill)",
        fileName: "moss - glow - v0 (ill).safetensors",
        filePath: archivedPath,
        modelPath: archivedPath,
        moveKey: "move:fedcba9876543210",
        baseModel: "Illustrious",
        publishedAt: "2025-06-01T10:00:00Z",
        archived: true,
        metadataOnly: true,
        hasLocalPreview: true,
        status: "ok",
      },
    ],
    versionOrder: [
      ["new", 0],
      ["new", 1],
      ["hidden", 0],
      ["saved", 0],
      ["saved", 1],
    ],
    ...extra,
  };
}

const rowsOf = (item = makeItem(), opts = {}) =>
  cardRows(item, { mode: "updates", showHidden: false, ...opts });

const ui = { mode: "updates", drive: "Z:", formatDate: fmt, now: NOW };

test("sizedImage asks Civitai for a smaller picture and leaves others alone", () => {
  assert.equal(
    sizedImage("https://image.civitai.com/x/abc/original=true/1.jpeg", 450),
    "https://image.civitai.com/x/abc/width=450/1.jpeg"
  );
  assert.equal(
    sizedImage("https://image.civitai.com/x/abc/width=1024/1.jpeg", 64),
    "https://image.civitai.com/x/abc/width=64/1.jpeg"
  );
  assert.equal(sizedImage("https://example.com/a.png", 64), "https://example.com/a.png");
  assert.equal(sizedImage("javascript:alert(1)", 64), "");
  assert.equal(sizedImage("", 64), "");
  assert.equal(PICTURE_WIDTH, 450);
  assert.equal(THUMB_WIDTH, 64);
});

test("archiveDriveLabel names the drive, never the path", () => {
  assert.equal(archiveDriveLabel("Z:\\models"), "Z:");
  assert.equal(archiveDriveLabel("\\\\nas\\share\\models"), "nas");
  assert.equal(archiveDriveLabel("/mnt/archive"), "archive");
  assert.equal(archiveDriveLabel(""), "");
});

test("typeLabel names the model types", () => {
  assert.equal(typeLabel("checkpoint"), "Checkpoint");
  assert.equal(typeLabel("lora"), "LoRA");
  assert.equal(typeLabel("vae"), "VAE");
  assert.equal(typeLabel("unet"), "UNet");
  assert.equal(typeLabel("embedding"), "Embedding");
  assert.equal(typeLabel("controlnet"), "Controlnet");
  assert.equal(typeLabel(""), "");
});

test("paidChip says what a Buzz release costs and until when", () => {
  const opts = { now: NOW, formatDate: fmt };
  assert.equal(paidChip({ paid: "permanent" }, opts).short, "Buzz · always");
  assert.equal(paidChip({ paid: "permanent" }, opts).kind, "permanent");
  assert.equal(paidChip({ paid: "early" }, opts).short, "Buzz · early");
  const dated = paidChip({ paid: "early", paidUntil: "2026-03-15T00:00:00Z" }, opts);
  assert.equal(dated.short, "Buzz · 5d left");
  assert.equal(
    dated.title,
    "Early access: costs Buzz to download until 2026-03-15, free after that."
  );
  assert.equal(paidChip({ paid: "" }, opts), null);
});

test("cardRows follows versionOrder and names each row", () => {
  const rows = rowsOf();
  assert.deepEqual(
    rows.map((r) => [r.kind, r.name]),
    [
      ["new", "h3 multi char swap (mmh3i)"],
      ["new", "v4"],
      ["drive", "xxl (moss)"],
      ["archive", "v1 (ill)"],
    ]
  );
  assert.deepEqual(
    rows.map((r) => r.key),
    ["v:202", "v:203", "f:" + saved.toLowerCase(), "f:" + archivedPath.toLowerCase()]
  );
  assert.equal(rows[0].nameTitle, "glow-h3");
  assert.equal(rows[1].nameTitle, "v4");
  assert.equal(rows[2].nameTitle, "moss - glow - v1 (ill).safetensors");
  assert.deepEqual(
    [rows[0].role, rows[0].label, rows[2].role, rows[3].role],
    ["new", "New", "saved", "archived"]
  );
  assert.equal(rows[0].date, "2026-03-01T10:00:00Z");
  assert.equal(rows[2].date, "2026-01-01T10:00:00Z");
  assert.deepEqual(rows[0].picture, {
    url: "https://image.civitai.com/x/abc/original=true/1.jpeg",
    type: "image",
  });
  assert.equal(rows[2].localPath, saved);
  assert.equal(rows[3].localPath, "");
  assert.equal(rows[0].issue, false);
});

test("cardRows falls back when the server sends no card fields", () => {
  const item = makeItem();
  for (const v of [...item.newVersions, ...item.localVersions]) {
    delete v.rowName;
    delete v.fileName;
    delete v.fileStem;
  }
  const rows = rowsOf(item);
  assert.deepEqual(
    rows.map((r) => r.name),
    ["h3", "v4", "v1", "v0"]
  );
  assert.equal(rows[2].nameTitle, "v1");
  const bare = makeItem({ versionOrder: undefined });
  assert.equal(rowsOf(bare).length, 4);
  assert.equal(rowsOf(bare, { showHidden: true }).length, 5);
  const nameless = makeItem();
  nameless.localVersions[0] = { modelPath: saved, filePath: saved };
  assert.equal(rowsOf(nameless)[2].name, "moss - glow - v1 (ill)");
  nameless.localVersions[0] = {};
  assert.equal(rowsOf(nameless)[2].name, "?");
});

test("cardRows: Issues has no releases; Show hidden adds them as Not interested", () => {
  assert.deepEqual(
    rowsOf(makeItem(), { mode: "issues", showHidden: true }).map((r) => r.kind),
    ["drive", "archive"]
  );
  assert.deepEqual(
    rowsOf(makeItem(), { showHidden: true }).map((r) => r.kind),
    ["new", "new", "hidden", "drive", "archive"]
  );
  const hidden = rowsOf(makeItem(), { showHidden: true })[2];
  assert.deepEqual([hidden.role, hidden.label, hidden.key], ["hidden", "Not interested", "v:204"]);
  assert.equal(hidden.slot.hidden, true);
});

test("cardRows marks an issue row", () => {
  const item = makeItem();
  item.localVersions[0].status = "not_found";
  const rows = rowsOf(item);
  assert.equal(rows[2].issue, true);
  assert.equal(rows[2].label, "No match");
});

test("cardRows: a release's slot downloads beside the drive file; a file's slot is its move key", () => {
  const rows = rowsOf();
  assert.deepEqual(
    [rows[0].slot.kind, rows[0].slot.dlKey, rows[0].slot.path],
    ["release", "version:202", saved]
  );
  assert.deepEqual([rows[0].slot.modelId, rows[0].slot.versionId], ["55", "202"]);
  assert.equal(rows[2].slot.kind, "file");
  assert.equal(rows[2].slot.dlKey, "move:0123456789abcdef");
  assert.equal(rows[2].slot.path, saved);
  assert.deepEqual(
    [rows[2].slot.place, rows[2].slot.canDelete, rows[2].slot.canMove],
    ["drive", true, true]
  );
  assert.deepEqual(
    [rows[3].slot.place, rows[3].slot.canDelete, rows[3].slot.canMove],
    ["archive", true, true]
  );
});

test("cardRows: deleted and info-only drive rows have no slot; a drive file with no version cannot move", () => {
  const item = makeItem();
  item.localVersions[0].deleted = true;
  assert.equal(rowsOf(item)[2].slot.kind, "none");
  assert.equal(rowsOf(item)[2].kind, "deleted");
  const info = makeItem();
  info.localVersions[0].metadataOnly = true;
  assert.equal(rowsOf(info)[2].slot.kind, "none");
  const unknown = makeItem();
  delete unknown.localVersions[0].versionId;
  const slot = rowsOf(unknown)[2].slot;
  assert.deepEqual([slot.canDelete, slot.canMove], [true, false]);
});

test("cardRows: a drive copy of an archived release offers Delete, not a second Archive", () => {
  const item = makeItem();
  item.localVersions[0].alsoArchived = "moss - glow - v1 (ill) copy.safetensors";
  const slot = rowsOf(item)[2].slot;
  assert.deepEqual([slot.canDelete, slot.canMove], [true, false]);
  const out = rowActionsHtml(slot, undefined, slotUi(), escapeHtml);
  assert.match(out, /data-row-action="delete"/);
  assert.doesNotMatch(out, /data-row-action="archive"|pi-box/);
  // The archived copy itself can still come back.
  assert.equal(rowsOf(item)[3].slot.canMove, true);
});

test("cardRows: a paid release says so in its slot", () => {
  const item = makeItem();
  item.newVersions[0].paid = "early";
  item.newVersions[0].paidUntil = "2026-03-15T00:00:00Z";
  const slot = rowsOf(item)[0].slot;
  assert.equal(slot.paid, true);
  assert.equal(slot.paidUntil, "2026-03-15T00:00:00Z");
  assert.equal(rowsOf(item)[1].slot.paid, false);
});

test("defaultRowKey: newest new in Updates, first issue in Issues, else the first row", () => {
  const rows = rowsOf();
  assert.equal(defaultRowKey(rows, "updates"), "v:202");
  assert.equal(defaultRowKey(rows.slice(2), "updates"), rows[2].key);
  const item = makeItem();
  item.localVersions.push({
    ...item.localVersions[0],
    modelPath: "D:\L\b.safetensors",
    status: "error",
  });
  item.versionOrder.push(["saved", 2]);
  const issueRows = rowsOf(item, { mode: "issues" });
  assert.equal(issueRows[2].issue, true);
  assert.equal(defaultRowKey(issueRows, "issues"), issueRows[2].key);
  assert.equal(defaultRowKey(rowsOf(), "issues"), "v:202");
  assert.equal(defaultRowKey([], "updates"), "");
});

test("pickRowKey: a remembered row that is gone falls back to the default", () => {
  const rows = rowsOf();
  assert.equal(pickRowKey(rows, "f:d:\\gone.safetensors", "updates"), "v:202");
  assert.equal(pickRowKey(rows, "f:" + saved.toLowerCase(), "updates"), "f:" + saved.toLowerCase());
  assert.equal(pickRowKey(rows, "", "updates"), "v:202");
});

test("infoLine says where each version is", () => {
  const rows = rowsOf(makeItem(), { showHidden: true });
  const [newRow, , hiddenRow, savedRow, archivedRow] = rows;
  assert.equal(infoLine(newRow, ui), "New · 2026-03-01 · not downloaded");
  assert.equal(infoLine(hiddenRow, ui), "Not interested · 2026-01-15 · not downloaded");
  assert.equal(infoLine(savedRow, ui), "Saved · 2026-01-01 · moss - glow - v1 (ill).safetensors");
  assert.equal(infoLine(archivedRow, ui), "Archived · 2025-06-01 · on Z:");
  assert.equal(infoLine({ ...newRow, date: "" }, ui), "New · not downloaded");
  assert.equal(infoLine(archivedRow, { ...ui, drive: "" }), "Archived · 2025-06-01");
});

test("infoLine in Issues is the issue message; a paid release shows its chip", () => {
  const item = makeItem();
  item.localVersions[0].status = "not_found";
  const issueRow = rowsOf(item)[2];
  assert.equal(infoLine(issueRow, { ...ui, mode: "issues" }), issueMessage(issueRow.version));
  assert.match(infoLine(issueRow, ui), /^No match · 2026-01-01 · /);
  const paid = makeItem();
  paid.newVersions[0].paid = "permanent";
  assert.equal(infoLine(rowsOf(paid)[0], ui), "New · 2026-03-01 · Buzz · always");
  const gone = makeItem();
  gone.localVersions[0].deleted = true;
  assert.equal(
    infoLine(rowsOf(gone)[2], ui),
    "Deleted · 2026-01-01 · moss - glow - v1 (ill).safetensors"
  );
});

// --- the slot -------------------------------------------------------------------------

const rec = (fields) => ({ id: "r1", key: "x", state: "waiting", written: 0, total: 0, ...fields });
const cells = (html) =>
  html.match(/<span class="cu-u-cell[^"]*">.*?<\/span>(?=<span class="cu-u-cell|$)/gs);
const slotUi = (fields) => ({
  canTrash: true,
  archiveSet: true,
  asking: "",
  now: NOW,
  formatDate: fmt,
  ...fields,
});
const release = (fields) => ({
  kind: "release",
  dlKey: "version:202",
  modelId: "55",
  versionId: "202",
  path: saved,
  hidden: false,
  paid: false,
  paidKind: "",
  paidUntil: "",
  place: "drive",
  canDelete: false,
  canMove: false,
  ...fields,
});
const file = (fields) => ({
  kind: "file",
  dlKey: "move:0123456789abcdef",
  modelId: "55",
  versionId: "101",
  path: saved,
  hidden: false,
  paid: false,
  paidKind: "",
  paidUntil: "",
  place: "drive",
  canDelete: true,
  canMove: true,
  ...fields,
});
const html = (slot, record, fields) => rowActionsHtml(slot, record, slotUi(fields), escapeHtml);

test("rowActionsHtml: a new release offers Download and Not interested; a hidden one Show again", () => {
  const out = html(release(), undefined);
  assert.match(out, /pi-download/);
  assert.match(out, /data-dl-action="download"/);
  assert.match(out, /data-hide-action="hide"/);
  assert.match(out, /pi-eye-slash/);
  assert.match(out, /title="Not interested"/);
  assert.doesNotMatch(out, /data-archive-action/);
  const hidden = html(release({ hidden: true }), undefined);
  assert.match(hidden, /data-hide-action="show"/);
  assert.match(hidden, /pi-eye"/);
  assert.match(hidden, /title="Show again"/);
});

test("rowActionsHtml: a paid release shows its Buzz chip where Download goes", () => {
  const out = html(
    release({ paid: true, paidKind: "early", paidUntil: "2026-03-15T00:00:00Z" }),
    undefined
  );
  assert.match(out, /class="cu-u-buzz"/);
  assert.match(out, /pi-bolt/);
  assert.match(out, /data-paid="early"/);
  assert.match(
    out,
    /title="Early access: costs Buzz to download until 2026-03-15, free after that\."/
  );
  assert.doesNotMatch(out, /data-dl-action="download"/);
  assert.match(out, /data-hide-action="hide"/);
  const forever = html(release({ paid: true, paidKind: "permanent" }), undefined);
  assert.match(forever, /data-paid="permanent"/);
});

test("rowActionsHtml: waiting, downloading, done and error keep two cells", () => {
  const waiting = html(release(), rec({ state: "waiting", waitingFor: "scan" }));
  assert.match(waiting, /pi-clock/);
  assert.match(waiting, /title="Waiting for the metadata fetch"/);
  assert.match(waiting, /data-dl-action="stop" data-request-id="r1"/);
  assert.match(waiting, /pi-times/);
  const running = html(file(), rec({ state: "downloading", written: 50, total: 100 }));
  assert.match(running, /role="progressbar"/);
  assert.match(running, /aria-valuenow="50"/);
  assert.match(running, /pi-stop-circle/);
  const done = html(release(), rec({ state: "done" }));
  assert.match(done, /pi-check/);
  const warned = html(release(), rec({ state: "done", warnings: ["the old file stayed"] }));
  assert.match(warned, /pi-exclamation-triangle/);
  assert.match(warned, /title="the old file stayed"/);
  const failed = html(release(), rec({ state: "error", error: "boom" }));
  assert.match(failed, /pi-refresh/);
  assert.match(failed, /data-dl-action="download"/);
  assert.match(failed, /title="Retry"/);
  assert.match(failed, /data-hide-action="hide"/);
});

test("rowActionsHtml: a file's failed move retries in the second cell", () => {
  const failed = html(file(), rec({ state: "error", error: "boom", to: "archive" }));
  const [first, second] = cells(failed);
  assert.match(first, /data-row-action="delete"/);
  assert.match(second, /pi-refresh/);
  assert.match(second, /data-row-action="archive"/);
  assert.match(second, /title="Retry"/);
  const back = html(file({ place: "archive" }), rec({ state: "error", to: "drive" }));
  assert.match(cells(back)[1], /data-row-action="unarchive"/);
});

test("rowActionsHtml: a file on the drive offers Delete and Archive; on the archive Delete and Unarchive", () => {
  const [del, move] = cells(html(file(), undefined));
  assert.match(del, /pi-trash/);
  assert.match(del, /data-row-action="delete"/);
  assert.match(del, new RegExp(`data-path="${reEsc(escapeHtml(saved))}"`));
  assert.match(move, /pi-box/);
  assert.match(move, /data-row-action="archive"/);
  assert.match(move, new RegExp(`title="${CARD_COPY.archiveTitle}"`));
  const onArchive = html(file({ place: "archive", path: archivedPath }), undefined);
  const [adel, aback] = cells(onArchive);
  assert.match(adel, new RegExp(`title="${CARD_COPY.deleteArchivedTitle.replace("'", "&#039;")}"`));
  assert.match(aback, /pi-undo/);
  assert.match(aback, /data-row-action="unarchive"/);
  assert.match(aback, new RegExp(`title="${CARD_COPY.unarchiveTitle}"`));
});

test("rowActionsHtml: no Archive without an archive folder; no Delete without the Recycle Bin", () => {
  const noArchive = html(file(), undefined, { archiveSet: false });
  assert.doesNotMatch(noArchive, /pi-box/);
  assert.match(noArchive, /data-row-action="delete"/);
  const noTrash = html(file(), undefined, { canTrash: false });
  assert.doesNotMatch(noTrash, /data-row-action/);
  const noMove = html(file({ canMove: false }), undefined);
  assert.doesNotMatch(noMove, /pi-box/);
  const noDelete = html(file({ canDelete: false }), undefined);
  assert.doesNotMatch(noDelete, /pi-trash/);
});

test("rowActionsHtml: Delete asks, then deletes", () => {
  const asking = html(file(), undefined, { asking: "confirming" });
  assert.match(asking, /data-row-action="delete"[^>]*data-confirm/);
  const deleting = html(file(), undefined, { asking: "deleting" });
  assert.match(deleting, /pi-spin pi-spinner/);
  assert.match(deleting, /data-deleting disabled/);
});

test("rowActionsHtml: a deleted file has two empty cells", () => {
  const out = html({ kind: "none" }, undefined);
  assert.equal((out.match(/class="cu-u-cell/g) || []).length, 2);
  assert.doesNotMatch(out, /<button|<i /);
});

test("every slot state is exactly two cells", () => {
  const records = [
    undefined,
    rec({ state: "waiting" }),
    rec({ state: "downloading", total: 10, written: 1 }),
    rec({ state: "done" }),
    rec({ state: "done", warnings: ["w"] }),
    rec({ state: "error", error: "e" }),
  ];
  const slots = [
    release(),
    release({ hidden: true }),
    release({ paid: true, paidKind: "early" }),
    file(),
    file({ place: "archive" }),
    { kind: "none" },
  ];
  for (const slot of slots) {
    for (const record of records) {
      for (const fields of [
        {},
        { archiveSet: false },
        { canTrash: false },
        { asking: "deleting" },
      ]) {
        const out = html(slot, record, fields);
        assert.equal(
          (out.match(/class="cu-u-cell/g) || []).length,
          2,
          JSON.stringify([slot, record, fields])
        );
      }
    }
  }
});

// --- the card -------------------------------------------------------------------------

const cardUi = (fields) => ({
  mode: "updates",
  selectedKey: "v:202",
  drive: "Z:",
  formatDate: fmt,
  copyTitle: (path) => `Copy ${path}`,
  records: new Map(),
  asking: new Map(),
  canTrash: true,
  archiveSet: true,
  now: NOW,
  ...fields,
});
const card = (item = makeItem(), fields, opts) => {
  const rows = rowsOf(item, opts);
  return updatesCardHtml(item, rows, cardUi(fields), escapeHtml);
};

test("updatesCardHtml: the pill, the N new tag, the names and the info line of the selected row", () => {
  const out = card();
  assert.match(out, /data-u-pill[^>]*>LoRA \| MiniMax H3 Image</);
  assert.match(out, /class="cu-u-new"[^>]*>2 new</);
  assert.match(out, /class="cu-g-title" title="glow lite">glow lite</);
  assert.match(out, /class="cu-g-creator" title="moss">moss</);
  assert.match(out, /data-u-info[^>]*>New · 2026-03-01 · not downloaded</);
  assert.match(out, /<img [^>]*src="https:\/\/image\.civitai\.com\/x\/abc\/width=450\/1\.jpeg"/);
  assert.match(
    out,
    /class="cu-g-avatar" src="https:\/\/image\.civitai\.com\/a\/b\/width=96\/me\.jpeg"/
  );
  assert.match(out, /class="cu-u-pic" data-u-pic/);
  assert.match(out, /aria-label="Open the preview of glow lite"/);
  assert.match(out, /1\.2k/);
});

test("updatesCardHtml: a card redrawn while Delete asks keeps the question on that row", () => {
  const out = card(makeItem(), { asking: new Map([[saved, "confirming"]]) });
  assert.match(out, /data-row-action="delete"[^>]*data-path="[^"]*v1 \(ill\)\.safetensors"[^>]*data-confirm/);
  assert.equal((out.match(/data-confirm/g) || []).length, 1);
});

test("rowActionsHtml: a finished move says Moved, a finished download Downloaded", () => {
  const moved = html(file(), rec({ state: "done", kind: "move" }));
  assert.match(moved, /pi-check[^]*title="Moved"|title="Moved"[^]*pi-check/);
  const downloaded = html(release(), rec({ state: "done", kind: "download" }));
  assert.match(downloaded, /title="Downloaded"/);
});

test("rowActionsHtml: a move that kept both copies gives the file its buttons back", () => {
  const warned = rec({ state: "done", kind: "move", warnings: ["Both copies are kept: …"] });
  const [del, move] = cells(html(file(), warned));
  assert.match(del, /data-row-action="delete"/);
  assert.match(move, /data-row-action="archive"/);
  const [adel, aback] = cells(html(file({ place: "archive", path: archivedPath }), warned));
  assert.match(adel, /data-row-action="delete"/);
  assert.match(aback, /data-row-action="unarchive"/);
  assert.doesNotMatch(html(file(), warned), /pi-exclamation-triangle|pi-check/);
  // The warning itself shows in place of the row's name.
  const item = makeItem();
  const records = new Map([["move:0123456789abcdef", { ...warned, key: "move:0123456789abcdef" }]]);
  const out = card(item, { records });
  assert.match(out, /data-dl-state="done" data-dl-warn="1"/);
  assert.match(out, /class="cu-u-fail" title="Both copies are kept: …">Both copies are kept: …</);
});

test("updatesCardHtml: one row per version, marked, with the selected one current", () => {
  const out = card();
  assert.equal((out.match(/data-u-row="/g) || []).length, 4);
  assert.equal((out.match(/aria-current="true"/g) || []).length, 1);
  assert.match(out, /data-u-row="v:202"[^>]*aria-current="true"/);
  assert.match(out, /data-pic="https:\/\/image\.civitai\.com\/x\/abc\/original=true\/1\.jpeg"/);
  assert.match(out, /data-pic-type="video"/);
  assert.match(out, new RegExp(`data-local-path="${reEsc(escapeHtml(saved))}"`));
  assert.match(out, /data-pill="LoRA \| Illustrious"/);
  assert.match(out, /data-info="Archived · 2025-06-01 · on Z:"/);
  assert.match(out, /role="list"/);
  assert.match(out, /role="listitem" tabindex="0"/);
  assert.match(out, /class="cu-u-tn"[^>]*><img [^>]*width=64\/1\.jpeg/);
  assert.match(out, /class="cu-u-dot" data-role="new" title="New" aria-label="New"/);
  assert.match(out, /class="cu-u-fail"/);
});

test("updatesCardHtml: saved rows copy their path; releases do not", () => {
  const out = card();
  assert.match(out, /class="cu-u-name cu-copy-path" data-path="[^"]*v1 \(ill\)\.safetensors"/);
  assert.match(out, /title="Copy D:\\L\\moss - glow - v1 \(ill\)\.safetensors"/);
  assert.match(out, /class="cu-u-name" title="glow-h3">h3 multi char swap \(mmh3i\)</);
});

test("updatesCardHtml: a row's slot carries its key and its JSON", () => {
  const out = card();
  assert.match(out, /class="cu-dl cu-u-acts" data-dl-key="version:202" data-slot="/);
  const attr = out.match(/data-dl-key="version:202" data-slot="([^"]*)"/)[1];
  const slot = JSON.parse(attr.replaceAll("&quot;", '"').replaceAll("&amp;", "&"));
  assert.equal(slot.dlKey, "version:202");
  assert.equal(slot.kind, "release");
});

test("updatesCardHtml: a running download shows in its row", () => {
  const records = new Map([
    ["version:202", rec({ key: "version:202", state: "error", error: "boom" })],
  ]);
  const out = card(makeItem(), { records });
  assert.match(out, /data-dl-state="error"/);
  assert.match(out, /class="cu-u-fail" title="boom">boom</);
});

test("updatesCardHtml: no N new tag for one release, no ↗ without an https link", () => {
  const item = makeItem({ modelUrl: "javascript:alert(1)" });
  item.newVersions.pop();
  item.versionOrder = [
    ["new", 0],
    ["saved", 0],
    ["saved", 1],
  ];
  const out = card(item);
  assert.doesNotMatch(out, /cu-u-new/);
  assert.doesNotMatch(out, /cu-g-open/);
  assert.match(card(), /<a class="cu-g-open" href="https:\/\/civitai\.red\/models\/55"/);
});

test("updatesCardHtml: a row without a picture says No preview", () => {
  const item = makeItem({ previewUrl: "" });
  delete item.newVersions[0].previewUrl;
  assert.match(card(item), /class="cu-g-noimg"[^>]*>No preview</);
  const video = card(makeItem(), { selectedKey: "v:203" });
  assert.match(
    video,
    /<video [^>]*src="https:\/\/image\.civitai\.com\/x\/abc\/original=true\/2\.mp4/
  );
});

test("updatesCardHtml: falls back to the old fields and keeps the stats row without numbers", () => {
  const item = makeItem({ title: undefined, creator: undefined, modelStats: undefined });
  const out = card(item);
  assert.match(out, /class="cu-g-title" title="MiniMax H3 Image">/);
  assert.match(out, /class="cu-g-foot"/);
  assert.match(out, /class="cu-g-likes"><\/span>/);
});

test("updatesCardHtml: a model without Civitai's numbers shows no stats", () => {
  // The server sends {} for a card none of whose files has them (most Issues cards).
  const avatarOnly = { creatorImage: "https://image.civitai.com/a/b/width=96/me.jpeg" };
  for (const modelStats of [{}, avatarOnly, { downloads: null }]) {
    const out = card(makeItem({ modelStats }));
    assert.doesNotMatch(out, /undefined|class="cu-g-stat"/, JSON.stringify(modelStats));
    assert.match(out, /class="cu-g-likes"><\/span>/);
  }
  const some = card(makeItem({ modelStats: { downloads: 5 } }));
  assert.match(some, /title="5 downloads"/);
  assert.match(some, /title="0 likes"/);
  assert.doesNotMatch(some, /undefined/);
});

test("updatesCardHtml: every text carries its full text in its title", () => {
  const out = card();
  for (const cls of [
    "cu-g-pill",
    "cu-g-creator",
    "cu-g-title",
    "cu-u-info",
    "cu-u-name",
    "cu-u-new",
  ]) {
    const tags = out.match(new RegExp(`<span class="${cls}(?: [^"]*)?"[^>]*>`, "g")) || [];
    assert.ok(tags.length, cls);
    for (const tag of tags) assert.match(tag, /title="/, tag);
  }
});

test("updatesCardHtml escapes names, paths and the slot JSON", () => {
  const item = makeItem({ title: '<b>"x"</b>', creator: "a&b" });
  item.localVersions[0].rowName = "<script>";
  item.localVersions[0].filePath = 'D:\\L\\"q".safetensors';
  const out = card(item);
  assert.doesNotMatch(out, /<script>|<b>/);
  assert.match(out, /&lt;script&gt;/);
  assert.match(out, /a&amp;b/);
  assert.match(out, /data-path="D:\\L\\&quot;q&quot;\.safetensors"/);
  assert.doesNotMatch(out, /data-slot="[^"]*"[^ >]*"/);
});

test("the card CSS keeps the layout still", () => {
  assert.match(UPDATES_CARD_CSS, /\.cu-item\.cu-u-card/);
  assert.match(UPDATES_CARD_CSS, /minmax\(300px, 1fr\)/);
  assert.match(UPDATES_CARD_CSS, /28px 7px minmax\(0, 1fr\) 52px/);
  assert.match(UPDATES_CARD_CSS, /height: 30px/);
  assert.match(UPDATES_CARD_CSS, /text-overflow: ellipsis/);
  assert.match(UPDATES_CARD_CSS, /data-mature="blur"/);
});
