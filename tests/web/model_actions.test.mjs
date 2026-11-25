import assert from "node:assert/strict";
import { test } from "node:test";

import {
  COPY,
  downloadRing,
  finishedText,
  iconButton,
  galleryActionHtml,
  galleryStateText,
  isPaid,
  latestRecords,
  percent,
  recordMessage,
  rowDeleteHtml,
  rowDownloadView,
  rowHideHtml,
  slotState,
  slotVersions,
  versionActionsHtml,
  waitingText,
  versionKey,
  galleryKey,
} from "../../web/model_actions.js";

// The panel's escapeHtml, as it is.
const escapeHtml = (text) =>
  String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const rec = (fields) => ({
  id: "r1",
  key: "version:201",
  state: "waiting",
  written: 0,
  total: 0,
  error: "",
  warnings: [],
  ...fields,
});

test("keys", () => {
  assert.equal(versionKey(201), "version:201");
  assert.equal(galleryKey("a.safetensors"), "gallery:a.safetensors");
});

test("percent rounds down, clamps and knows an unknown size", () => {
  assert.equal(percent({ written: 50, total: 200 }), 25);
  assert.equal(percent({ written: 5, total: 0 }), null);
  assert.equal(percent({ written: 300, total: 200 }), 100);
  assert.equal(percent({ written: 1, total: 3 }), 33);
});

test("latestRecords keeps the newest record per key", () => {
  const map = latestRecords([
    rec({ id: "a", state: "error" }),
    rec({ id: "b", key: "version:202" }),
    rec({ id: "c", state: "done" }),
  ]);
  assert.equal(map.size, 2);
  assert.equal(map.get("version:201").id, "c");
});

test("slotState maps every record state", () => {
  assert.equal(slotState(undefined), "idle");
  for (const state of ["waiting", "downloading", "done", "error"]) {
    assert.equal(slotState(rec({ state })), state);
  }
  assert.equal(slotState(rec({ state: "stopped" })), "idle");
});

const slotOf = (html) => html.match(/^<span class="cu-slot"[^>]*>([\s\S]*)<\/span>$/);
const icons = (html) =>
  [...html.matchAll(/<i class="pi ([^"]+)" aria-hidden="true"><\/i>/g)].map((m) => m[1]);
const buttons = (html) => [...html.matchAll(/<button\b[^>]*>/g)].map((m) => m[0]);

test("versionActionsHtml offers Replace only when the version can be replaced", () => {
  const plain = versionActionsHtml({ paid: false, canReplace: false }, undefined, escapeHtml);
  assert.ok(plain.includes('data-dl-action="download"'));
  assert.ok(!plain.includes('data-dl-action="replace"'));
  assert.deepEqual(icons(plain), ["pi-download"]);
  const both = versionActionsHtml({ paid: false, canReplace: true }, undefined, escapeHtml);
  assert.ok(both.includes('data-dl-action="replace"'));
  assert.deepEqual(icons(both), ["pi-download", "pi-sync"]);
  assert.ok(
    both.includes("Download this release, then send the version you have to the Recycle Bin")
  );
  for (const b of buttons(both)) {
    assert.ok(b.includes('class="cu-icon-btn"'));
    assert.ok(/ title="[^"]+"/.test(b));
    assert.ok(/ aria-label="[^"]+"/.test(b));
  }
  assert.ok(buttons(plain)[0].includes('aria-label="Download this release"'));
});

test("versionActionsHtml shows Early access instead of the buttons", () => {
  const html = versionActionsHtml({ paid: true, canReplace: true }, undefined, escapeHtml);
  assert.ok(html.includes('<span class="cu-dl-note">Early access</span>'));
  assert.ok(!html.includes("data-dl-action"));
  assert.ok(slotOf(html));
});

test("versionActionsHtml: waiting is a clock with its reason and Cancel", () => {
  const waiting = versionActionsHtml(
    { paid: false, canReplace: false },
    rec({ id: "r9", waitingFor: "check-updates" }),
    escapeHtml
  );
  assert.deepEqual(icons(waiting), ["pi-clock", "pi-times"]);
  assert.ok(waiting.includes('title="Waiting for the check"'));
  assert.ok(waiting.includes('data-dl-action="stop" data-request-id="r9"'));
  assert.ok(waiting.includes('aria-label="Cancel"'));
  assert.ok(!waiting.includes(">Waiting"));
});

test("versionActionsHtml: a download is a ring where Download was, and Stop", () => {
  const running = versionActionsHtml(
    { paid: false, canReplace: false },
    rec({ id: "r9", state: "downloading", written: 42, total: 100 }),
    escapeHtml
  );
  assert.ok(running.includes("cu-dl-ring"), running);
  assert.ok(running.includes('aria-valuenow="42"') && running.includes('title="42%"'));
  assert.ok(!running.includes("is-spin"));
  assert.ok(!running.includes("cu-dl-pct"));
  assert.deepEqual(icons(running), ["pi-stop-circle"]);
  assert.ok(running.includes('aria-label="Stop"'));
  // Before the first bytes and while the file is checked, the ring spins.
  const starting = versionActionsHtml({ paid: false }, rec({ state: "downloading" }), escapeHtml);
  assert.ok(starting.includes("is-spin") && starting.includes('title="Starting…"'), starting);
  const checking = versionActionsHtml(
    { paid: false },
    rec({ state: "downloading", written: 100, total: 100 }),
    escapeHtml
  );
  assert.ok(
    checking.includes("is-spin") && checking.includes('title="Checking the file…"'),
    checking
  );
});

test("versionActionsHtml: Replace names the version it replaces and the file that goes", () => {
  const html = versionActionsHtml(
    { canReplace: true, replaces: { name: "v1", file: "glow - v1.safetensors" }, version: "v2" },
    undefined,
    escapeHtml
  );
  const replace = html.match(/<button[^>]*data-dl-action="replace"[^>]*>/)[0];
  assert.ok(
    replace.includes(
      'title="Replace v1 with v2: downloads v2, then sends glow - v1.safetensors to the Recycle Bin"'
    ),
    replace
  );
});

test("versionActionsHtml: done is a check, error is Retry", () => {
  const done = versionActionsHtml({ paid: false }, rec({ state: "done" }), escapeHtml);
  assert.deepEqual(icons(done), ["pi-check"]);
  assert.ok(done.includes('title="Downloaded"'));
  assert.ok(!done.includes("data-dl-action") && !done.includes("<button"));
  const failed = versionActionsHtml(
    { paid: false, canReplace: true },
    rec({ state: "error", error: "x" }),
    escapeHtml
  );
  assert.deepEqual(icons(failed), ["pi-refresh"]);
  assert.ok(failed.includes('data-dl-action="download"'));
  assert.ok(failed.includes('aria-label="Retry"'));
  // A failed Replace retries as a Replace.
  const failedReplace = versionActionsHtml(
    { paid: false, canReplace: true },
    rec({ state: "error", replace: true }),
    escapeHtml
  );
  assert.ok(failedReplace.includes('data-dl-action="replace"'));
  assert.ok(failedReplace.includes(`title="${COPY.replaceTitle}"`));
});

test("every download state sits in one slot element", () => {
  const states = [
    [{ paid: false, canReplace: true }, undefined],
    [{ paid: false }, rec()],
    [{ paid: false }, rec({ state: "downloading", written: 1, total: 2 })],
    [{ paid: false }, rec({ state: "done" })],
    [{ paid: false }, rec({ state: "error" })],
    [{ paid: true }, undefined],
  ];
  for (const [slot, record] of states) {
    const html = versionActionsHtml(slot, record, escapeHtml);
    assert.ok(slotOf(html), html);
    // At most two icon buttons, so one fixed width fits all.
    assert.ok(buttons(html).length <= 2);
  }
});

test("rowDownloadView: no record, waiting, downloading, done, error", () => {
  assert.deepEqual(rowDownloadView(undefined), {
    state: "idle",
    pct: null,
    failure: "",
    warning: "",
  });
  assert.deepEqual(rowDownloadView(rec()), {
    state: "waiting",
    pct: null,
    failure: "",
    warning: "",
  });
  assert.deepEqual(rowDownloadView(rec({ state: "downloading", written: 25, total: 100 })), {
    state: "downloading",
    pct: 25,
    failure: "",
    warning: "",
  });
  assert.equal(rowDownloadView(rec({ state: "downloading" })).pct, null);
  assert.deepEqual(rowDownloadView(rec({ state: "done", written: 5, total: 5 })), {
    state: "done",
    pct: null,
    failure: "",
    warning: "",
  });
  assert.deepEqual(rowDownloadView(rec({ state: "error", error: "No space left" })), {
    state: "error",
    pct: null,
    failure: "No space left",
    warning: "",
  });
});

test("rowHideHtml: Hide and Unhide are icon buttons", () => {
  const hide = rowHideHtml(false, "7", "8", escapeHtml);
  assert.deepEqual(icons(hide), ["pi-eye-slash"]);
  assert.ok(hide.includes('data-archive-action="archive"'));
  assert.ok(hide.includes('aria-label="Hide"') && hide.includes('title="Hide"'));
  const unhide = rowHideHtml(true, "7", "8", escapeHtml);
  assert.deepEqual(icons(unhide), ["pi-eye"]);
  assert.ok(unhide.includes('data-archive-action="restore"'));
  assert.ok(unhide.includes('aria-label="Unhide"'));
  assert.ok(!rowHideHtml(false, '"><b>', "8", escapeHtml).includes("<b>"));
});

test("versionActionsHtml escapes the request id", () => {
  const html = versionActionsHtml({ paid: false }, rec({ id: '"><img src=x>' }), escapeHtml);
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&quot;&gt;&lt;img src=x&gt;"));
});

test("recordMessage gives the API key message as sent", () => {
  const text =
    "Civitai needs an API key for this download: Settings → Warp Studio → Civitai Network → API Key";
  assert.equal(recordMessage(rec({ state: "error", error: text })), text);
  assert.equal(recordMessage(rec({ state: "done", warnings: ["a", "b"] })), "a b");
  assert.equal(recordMessage(undefined), "");
});

test("isPaid reads paid and the old availability", () => {
  assert.equal(isPaid({ paid: "early" }), true);
  assert.equal(isPaid({ paid: "permanent" }), true);
  assert.equal(isPaid({ availability: "EarlyAccess" }), true);
  assert.equal(isPaid({ paid: "", availability: "Public" }), false);
  assert.equal(isPaid({}), false);
});

test("finishedText counts what finished", () => {
  assert.equal(
    finishedText({ downloaded: 2, failed: 1, stopped: 0 }),
    "Done — 2 downloaded, 1 failed"
  );
  assert.equal(
    finishedText({ downloaded: 2, failed: 0, stopped: 1 }),
    "Done — 2 downloaded, 1 stopped"
  );
  assert.equal(finishedText({ downloaded: 1 }), "Done — 1 downloaded");
  assert.ok(COPY.download);
});

test("finishedText counts moves apart from downloads", () => {
  assert.equal(finishedText({ moved: 1 }), "Done — 1 moved");
  assert.equal(finishedText({ downloaded: 2 }), "Done — 2 downloaded");
  assert.equal(
    finishedText({ downloaded: 2, moved: 1, failed: 1 }),
    "Done — 2 downloaded, 1 moved, 1 failed"
  );
});

test("the card's building blocks are exported", () => {
  assert.match(iconButton("pi-x", "Close", ' data-a="1"'), /title="Close" aria-label="Close"/);
  assert.match(downloadRing(40), /aria-valuenow="40"/);
  assert.equal(waitingText({ waitingFor: "scan" }), "Waiting for the metadata fetch");
  assert.equal(waitingText({}), "");
});

const dlrec = (fields) => ({ id: "r1", key: "gallery:a.safetensors", ...fields });
const onDisk = { missing: false };
const gone = { missing: true };
const ui = (fields) => ({ canTrash: true, confirming: false, deleting: false, ...fields });

test("galleryActionHtml offers Download on a missing card and Delete on one on disk", () => {
  const download = galleryActionHtml(gone, undefined, ui(), escapeHtml);
  assert.ok(download.includes('data-g-action="download"'));
  assert.ok(download.includes(">Download<"));
  assert.ok(!download.includes("delete"));
  assert.equal(
    galleryActionHtml(gone, dlrec({ state: "error", error: "x" }), ui(), escapeHtml),
    download
  );
  const cancel = galleryActionHtml(gone, dlrec({ state: "waiting" }), ui(), escapeHtml);
  assert.ok(cancel.includes('data-g-action="stop"') && cancel.includes('data-request-id="r1"'));
  assert.ok(cancel.includes(">Cancel<"));
  const stop = galleryActionHtml(gone, dlrec({ state: "downloading" }), ui(), escapeHtml);
  assert.ok(stop.includes(">Stop<"));
  assert.equal(galleryActionHtml(gone, dlrec({ state: "done" }), ui(), escapeHtml), "");
  const del = galleryActionHtml(onDisk, undefined, ui(), escapeHtml);
  assert.ok(del.includes('data-g-action="delete"'));
  assert.ok(del.includes(">Delete<"));
  assert.ok(del.includes('title="Send this LoRA and its sidecars to the Recycle Bin"'));
  assert.ok(!del.includes("data-confirm"));
  assert.ok(!del.includes("download"));
});

test("galleryActionHtml escapes the request id", () => {
  const html = galleryActionHtml(gone, dlrec({ id: '"><b>', state: "waiting" }), ui(), escapeHtml);
  assert.ok(!html.includes("<b>"));
});

test("galleryActionHtml asks before deleting", () => {
  const ask = galleryActionHtml(onDisk, undefined, ui({ confirming: true }), escapeHtml);
  assert.ok(ask.includes(">Delete?<"));
  assert.ok(ask.includes("data-confirm"));
  assert.ok(!ask.includes("disabled"));
  const busy = galleryActionHtml(onDisk, undefined, ui({ deleting: true }), escapeHtml);
  assert.ok(busy.includes(">Deleting…<"));
  assert.ok(busy.includes("disabled"));
});

test("galleryActionHtml offers no Delete without the Recycle Bin", () => {
  assert.equal(galleryActionHtml(onDisk, undefined, ui({ canTrash: false }), escapeHtml), "");
  // A download needs no Recycle Bin.
  assert.ok(
    galleryActionHtml(gone, undefined, ui({ canTrash: false }), escapeHtml).includes("Download")
  );
});

test("galleryStateText shows Waiting, the percentage and the error, and Downloaded only just after", () => {
  assert.equal(galleryStateText(undefined, {}), "");
  assert.equal(galleryStateText(dlrec({ state: "waiting" }), {}), "Waiting");
  assert.equal(
    galleryStateText(dlrec({ state: "downloading", written: 42, total: 100 }), {}),
    "42%"
  );
  assert.equal(galleryStateText(dlrec({ state: "downloading" }), {}), "Downloading");
  assert.equal(galleryStateText(dlrec({ state: "done" }), {}), "");
  assert.equal(galleryStateText(dlrec({ state: "done" }), { justDone: true }), "Downloaded");
  assert.equal(galleryStateText(dlrec({ state: "error", error: "Nope." }), {}), "Nope.");
  assert.equal(galleryStateText(dlrec({ state: "waiting" }), { justDone: true }), "Waiting");
});

test("galleryActionHtml names the kind of file in Delete's tooltip", () => {
  const html = (kind) => galleryActionHtml({ missing: false, kind }, undefined, ui(), escapeHtml);
  assert.ok(html("checkpoints").includes("Send this checkpoint and its sidecars"));
  assert.ok(html("embeddings").includes("Send this embedding and its sidecars"));
  assert.ok(html("loras").includes("Send this LoRA and its sidecars"));
  assert.ok(html(undefined).includes("Send this LoRA and its sidecars"));
});

test("a waiting record names the job it waits for", () => {
  const waiting = (waitingFor) => rec({ state: "waiting", waitingFor });
  // The overlay and the slot say it; the message line stays empty.
  assert.equal(recordMessage(waiting("check-updates")), "");
  assert.equal(galleryStateText(waiting("check-updates"), {}), "Waiting for the check");
  assert.equal(galleryStateText(waiting("rename-apply"), {}), "Waiting for the rename");
  assert.equal(galleryStateText(waiting("something-new"), {}), "Waiting for another job");
  // Without a waitingFor, today's text.
  assert.equal(recordMessage(rec({ state: "waiting" })), "");
  assert.equal(galleryStateText(rec({ state: "waiting" }), {}), "Waiting");
});

test("a waiting version row shows what it waits for in the slot's title and no second line", () => {
  const slot = { paid: false, canReplace: true };
  const html = versionActionsHtml(slot, rec({ waitingFor: "check-updates" }), escapeHtml);
  assert.ok(html.includes('title="Waiting for the check"'));
  assert.ok(html.includes('aria-label="Cancel"'));
  assert.equal(recordMessage(rec({ waitingFor: "check-updates" })), "");
  assert.ok(
    versionActionsHtml(slot, rec({ waitingFor: "other-job" }), escapeHtml).includes(
      'title="Waiting for another job"'
    )
  );
  assert.ok(versionActionsHtml(slot, rec(), escapeHtml).includes('title="Waiting"'));
  const hostile = versionActionsHtml(slot, rec({ id: "r1", waitingFor: "<b>" }), escapeHtml);
  assert.ok(!hostile.includes("<b>"));
});

// A version row in an Updates or Issues card: Delete for a file on this drive.
const onDrive = (fields) => ({ modelPath: "D:/m/a.safetensors", ...fields });

test("rowDeleteHtml: a file on this drive gets Delete with its path", () => {
  const html = rowDeleteHtml(onDrive(), { canTrash: true }, escapeHtml);
  assert.ok(html.includes('data-row-action="delete"'));
  assert.ok(html.includes('data-path="D:/m/a.safetensors"'));
  assert.deepEqual(icons(html), ["pi-trash"]);
  assert.ok(html.includes('class="cu-icon-btn cu-ver-del"'));
  assert.ok(html.includes(`aria-label="${COPY.deleteTitleFor("version")}"`));
});

test("rowDeleteHtml: never for the archive, an info file alone, no path, or without the Recycle Bin", () => {
  const ui = { canTrash: true };
  assert.equal(rowDeleteHtml(onDrive({ archived: true }), ui, escapeHtml), "");
  assert.equal(rowDeleteHtml(onDrive({ metadataOnly: true }), ui, escapeHtml), "");
  assert.equal(rowDeleteHtml(onDrive({ deleted: true }), ui, escapeHtml), "");
  assert.equal(rowDeleteHtml(onDrive({ modelPath: "" }), ui, escapeHtml), "");
  assert.equal(rowDeleteHtml(onDrive(), { canTrash: false }, escapeHtml), "");
});

test("rowDeleteHtml: asks, then deletes", () => {
  const asking = rowDeleteHtml(onDrive(), { canTrash: true, confirming: true }, escapeHtml);
  assert.ok(asking.includes("data-confirm"));
  assert.deepEqual(icons(asking), ["pi-trash"]);
  assert.equal(COPY.deleteConfirmTitle, "Click again to send it to the Recycle Bin");
  assert.ok(asking.includes(`title="${COPY.deleteConfirmTitle}"`));
  assert.ok(asking.includes(`aria-label="${COPY.deleteConfirmTitle}"`));
  const deleting = rowDeleteHtml(onDrive(), { canTrash: true, deleting: true }, escapeHtml);
  assert.ok(deleting.includes("disabled"));
  assert.deepEqual(icons(deleting), ["pi-spin pi-spinner"]);
  assert.ok(deleting.includes(`aria-label="${COPY.deleting}"`));
});

test("rowDeleteHtml: the path is escaped", () => {
  const html = rowDeleteHtml(
    onDrive({ modelPath: 'D:/m/"x"<b>.safetensors' }),
    { canTrash: true },
    escapeHtml
  );
  assert.ok(!html.includes("<b>"));
  assert.ok(html.includes("&quot;x&quot;"));
});

test("a finished download with warnings says so in its row", () => {
  const warned = rec({
    state: "done",
    written: 5,
    total: 5,
    warnings: ["The old version couldn't go to the Recycle Bin: it is in use."],
  });
  assert.deepEqual(rowDownloadView(warned), {
    state: "done",
    pct: null,
    failure: "",
    warning: "The old version couldn't go to the Recycle Bin: it is in use.",
  });
  const html = versionActionsHtml({}, warned, escapeHtml);
  assert.ok(html.includes("pi-exclamation-triangle"), html);
  assert.ok(!html.includes("pi-check"), html);
  assert.ok(html.includes("it is in use."), html);
  const clean = versionActionsHtml({}, rec({ state: "done", written: 5, total: 5 }), escapeHtml);
  assert.ok(clean.includes("pi-check") && !clean.includes("pi-exclamation-triangle"), clean);
  assert.equal(rowDownloadView(rec({ state: "done", written: 5, total: 5 })).warning, "");
});

test("slotVersions: a download goes beside the version on the drive, and Replace takes only that one", () => {
  const drive = { modelPath: "D:\\l\\pony\\a v2.safetensors" };
  const archived = { modelPath: "Z:\\l\\pony\\a v1.safetensors", archived: true };
  const deleted = { modelPath: "D:\\l\\pony\\a v0.safetensors", deleted: true };
  assert.deepEqual(slotVersions([archived, drive]), { reference: drive, current: drive });
  // Only archived: the server finds the drive folder that mirrors it; nothing to replace.
  assert.deepEqual(slotVersions([archived]), { reference: archived, current: undefined });
  assert.deepEqual(slotVersions([deleted, archived]), { reference: archived, current: undefined });
  // Only a deleted row: its folder is still where the release goes.
  assert.deepEqual(slotVersions([deleted]), { reference: deleted, current: undefined });
  assert.deepEqual(slotVersions(undefined), { reference: undefined, current: undefined });
});
