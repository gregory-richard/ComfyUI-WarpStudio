import assert from "node:assert/strict";
import { test } from "node:test";

import { statusRowView } from "../../web/status_row.js";

const idle = { running: false, jobText: "", errorText: "", noticeText: "" };

test("statusRowView: idle with a status keeps the text and hides progress and controls", () => {
  const v = statusRowView({ ...idle, jobText: "Done \u2014 35 updates found" });
  assert.deepEqual(v, {
    text: "Done \u2014 35 updates found",
    level: "info",
    progress: false,
    controls: false,
    noticeAct: false,
  });
});

test("statusRowView: idle with nothing is an empty info row", () => {
  const v = statusRowView(idle);
  assert.equal(v.text, "");
  assert.equal(v.level, "info");
  assert.equal(v.progress, false);
});

test("statusRowView: an error wins over the job text, in the error level", () => {
  const v = statusRowView({ ...idle, jobText: "Done", errorText: "Check failed" });
  assert.equal(v.text, "Check failed");
  assert.equal(v.level, "error");
  assert.equal(v.progress, false);
  assert.equal(v.controls, false);
});

test("statusRowView: running shows progress, controls and the job text", () => {
  const v = statusRowView({ ...idle, running: true, jobText: "Checking 3 of 9" });
  assert.deepEqual(v, {
    text: "Checking 3 of 9",
    level: "info",
    progress: true,
    controls: true,
    noticeAct: false,
  });
});

test("statusRowView: a notice while idle shows its text and the button", () => {
  const v = statusRowView({ ...idle, jobText: "Results from 2m ago", noticeText: "Results from 2m ago" });
  assert.equal(v.text, "Results from 2m ago");
  assert.equal(v.noticeAct, true);
  assert.equal(v.progress, false);
});

test("statusRowView: a notice never shows while running", () => {
  const v = statusRowView({ ...idle, running: true, jobText: "Working", noticeText: "Results from 2m ago" });
  assert.equal(v.noticeAct, false);
  assert.equal(v.text, "Working");
});

test("statusRowView: an error hides the notice button", () => {
  const v = statusRowView({ ...idle, noticeText: "Results from 2m ago", errorText: "Oops" });
  assert.equal(v.noticeAct, false);
  assert.equal(v.level, "error");
});
