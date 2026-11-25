import assert from "node:assert/strict";
import { test } from "node:test";

import { statusRowView } from "../../web/status_row.js";

const idle = { running: false, jobText: "", errorText: "", flash: false, onButton: false };

test("statusRowView: nothing to say hides the pill", () => {
  const v = statusRowView(idle);
  assert.equal(v.visible, false);
  assert.equal(v.text, "");
});

test("statusRowView: a running job shows its text, progress and controls", () => {
  const v = statusRowView({ ...idle, running: true, jobText: "Processing 6 of 20" });
  assert.deepEqual(v, {
    visible: true,
    text: "Processing 6 of 20",
    level: "info",
    progress: true,
    controls: true,
    dismiss: false,
  });
});

test("statusRowView: an error during a job does not hide the job's text", () => {
  const v = statusRowView({ ...idle, running: true, jobText: "Processing", errorText: "Hiccup" });
  assert.equal(v.text, "Processing");
  assert.equal(v.level, "info");
});

test("statusRowView: the result of a finished job shows only while it flashes", () => {
  const done = { ...idle, jobText: "Done — 35 updates found" };
  assert.equal(statusRowView({ ...done, flash: true }).visible, true);
  assert.equal(statusRowView({ ...done, flash: true }).text, "Done — 35 updates found");
  assert.equal(statusRowView({ ...done, flash: true }).controls, false);
  assert.equal(statusRowView(done).visible, false);
});

test("statusRowView: an error stays, in the error level, with a dismiss", () => {
  const v = statusRowView({ ...idle, jobText: "Done", errorText: "Check failed" });
  assert.equal(v.visible, true);
  assert.equal(v.text, "Check failed");
  assert.equal(v.level, "error");
  assert.equal(v.dismiss, true);
  assert.equal(v.progress, false);
});

test("statusRowView: a check shown on its button keeps the pill in", () => {
  const v = statusRowView({ ...idle, running: true, jobText: "Processing 6 of 20", onButton: true });
  assert.equal(v.visible, false);
  assert.equal(v.controls, false);
  // Its result flashes on the button too.
  assert.equal(statusRowView({ ...idle, jobText: "Done — 3 updates found", flash: true, onButton: true }).visible, false);
});

test("statusRowView: an error while a check runs on its button shows in the pill", () => {
  const v = statusRowView({ ...idle, running: true, jobText: "Processing", errorText: "Reconnecting... (Attempt 1/5)", onButton: true });
  assert.equal(v.visible, true);
  assert.equal(v.text, "Reconnecting... (Attempt 1/5)");
  assert.equal(v.progress, false);
});

test("statusRowView: an error still shows in the pill when the check is on its button", () => {
  const v = statusRowView({ ...idle, jobText: "Done", errorText: "The archive folder can't be reached.", flash: true, onButton: true });
  assert.equal(v.visible, true);
  assert.equal(v.text, "The archive folder can't be reached.");
  assert.equal(v.level, "error");
  assert.equal(v.dismiss, true);
});
