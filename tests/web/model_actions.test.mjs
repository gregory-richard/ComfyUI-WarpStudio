import assert from "node:assert/strict";
import { test } from "node:test";

import {
  COPY,
  finishedText,
  isPaid,
  latestRecords,
  percent,
  recordMessage,
  slotState,
  versionActionsHtml,
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

test("versionActionsHtml offers Replace only when the version can be replaced", () => {
  const plain = versionActionsHtml({ paid: false, canReplace: false }, undefined, escapeHtml);
  assert.ok(plain.includes('data-dl-action="download"'));
  assert.ok(!plain.includes('data-dl-action="replace"'));
  const both = versionActionsHtml({ paid: false, canReplace: true }, undefined, escapeHtml);
  assert.ok(both.includes('data-dl-action="replace"'));
  assert.ok(
    both.includes("Download this release, then send the version you have to the Recycle Bin")
  );
  const afterError = versionActionsHtml(
    { paid: false, canReplace: true },
    rec({ state: "error" }),
    escapeHtml
  );
  assert.ok(afterError.includes('data-dl-action="download"'));
});

test("versionActionsHtml shows Early access instead of the buttons", () => {
  const html = versionActionsHtml({ paid: true, canReplace: true }, undefined, escapeHtml);
  assert.ok(html.includes('<span class="cu-dl-note">Early access</span>'));
  assert.ok(!html.includes("data-dl-action"));
});

test("versionActionsHtml shows Waiting with Cancel and the percentage with Stop", () => {
  const waiting = versionActionsHtml(
    { paid: false, canReplace: false },
    rec({ id: "r9" }),
    escapeHtml
  );
  assert.ok(waiting.includes(">Waiting<"));
  assert.ok(waiting.includes('data-dl-action="stop" data-request-id="r9"'));
  assert.ok(waiting.includes(">Cancel<"));
  const running = versionActionsHtml(
    { paid: false, canReplace: false },
    rec({ id: "r9", state: "downloading", written: 42, total: 100 }),
    escapeHtml
  );
  assert.ok(running.includes(">42%<"));
  assert.ok(running.includes(">Stop<"));
  const unknown = versionActionsHtml({ paid: false }, rec({ state: "downloading" }), escapeHtml);
  assert.ok(unknown.includes(">Downloading<"));
  const done = versionActionsHtml({ paid: false }, rec({ state: "done" }), escapeHtml);
  assert.ok(done.includes(">Downloaded<"));
  assert.ok(!done.includes("data-dl-action"));
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
