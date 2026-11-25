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
  rowDownloadView,
  slotState,
  slotVersions,
  waitingText,
  versionKey,
  galleryKey,
} from "../../web/model_actions.js";
import * as actions from "../../web/model_actions.js";

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

test("a finished download with warnings says so in its row", () => {
  const warned = rec({
    state: "done",
    written: 5,
    total: 5,
    warnings: ["Both copies are kept: x.safetensors could not go to the Recycle Bin (in use)."],
  });
  assert.deepEqual(rowDownloadView(warned), {
    state: "done",
    pct: null,
    failure: "",
    warning: "Both copies are kept: x.safetensors could not go to the Recycle Bin (in use).",
  });
  assert.equal(rowDownloadView(rec({ state: "done", written: 5, total: 5 })).warning, "");
});

test("Replace and the old row markup have left the panel", () => {
  for (const name of ["versionActionsHtml", "rowHideHtml", "rowDeleteHtml"]) {
    assert.equal(actions[name], undefined, name);
  }
  for (const key of ["replace", "replaceTitle", "hide", "unhide"]) {
    assert.equal(COPY[key], undefined, key);
  }
});

test("slotVersions: a download goes beside the version on the drive", () => {
  const drive = { modelPath: "D:\\l\\pony\\a v2.safetensors" };
  const archived = { modelPath: "Z:\\l\\pony\\a v1.safetensors", archived: true };
  const deleted = { modelPath: "D:\\l\\pony\\a v0.safetensors", deleted: true };
  assert.deepEqual(slotVersions([archived, drive]), { reference: drive });
  // Only archived: the server finds the drive folder that mirrors it.
  assert.deepEqual(slotVersions([archived]), { reference: archived });
  assert.deepEqual(slotVersions([deleted, archived]), { reference: archived });
  // Only a deleted row: its folder is still where the release goes.
  assert.deepEqual(slotVersions([deleted]), { reference: deleted });
  assert.deepEqual(slotVersions(undefined), { reference: undefined });
});
