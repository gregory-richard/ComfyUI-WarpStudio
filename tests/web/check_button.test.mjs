import assert from "node:assert/strict";
import { test } from "node:test";

import { checkButtonView, freshCheckDialogHtml } from "../../web/check_button.js";

const escapeHtml = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const idle = { checking: false, status: "", progress: 0, total: 0, flashText: "" };

test("checkButtonView: idle, the button checks", () => {
  assert.deepEqual(checkButtonView(idle), { label: "Check for Updates", fill: null, busy: false, spin: false });
});

test("checkButtonView: a running check is the progress, filled by files done", () => {
  assert.deepEqual(checkButtonView({ ...idle, checking: true, status: "running", progress: 491, total: 746 }), {
    label: "Checking 491 of 746",
    fill: 491 / 746,
    busy: true,
    spin: false,
  });
});

test("checkButtonView: a check without a total yet spins", () => {
  assert.deepEqual(checkButtonView({ ...idle, checking: true, status: "queued" }), {
    label: "Starting the check…",
    fill: null,
    busy: true,
    spin: true,
  });
});

test("checkButtonView: a paused check says so and keeps its fill", () => {
  const v = checkButtonView({ ...idle, checking: true, status: "paused", progress: 10, total: 40 });
  assert.equal(v.label, "Paused at 10 of 40");
  assert.equal(v.fill, 0.25);
  assert.equal(v.busy, true);
});

test("checkButtonView: the fill never runs past the end", () => {
  assert.equal(checkButtonView({ ...idle, checking: true, status: "running", progress: 50, total: 40 }).fill, 1);
});

test("checkButtonView: a finished check's result flashes on the button", () => {
  assert.deepEqual(checkButtonView({ ...idle, flashText: "Done — 28 updates found" }), {
    label: "Done — 28 updates found",
    fill: null,
    busy: false,
    spin: false,
  });
});

test("freshCheckDialogHtml: asks with the results' age, Check again first", () => {
  const html = freshCheckDialogHtml("5 min ago", escapeHtml);
  assert.match(html, /These results are from 5 min ago\./);
  assert.ok(html.indexOf('value="check"') < html.indexOf('value="keep"'));
  assert.match(html, /<button[^>]*value="check"[^>]*>Check again<\/button>/);
  assert.match(html, /<button[^>]*value="keep"[^>]*>Keep these<\/button>/);
  assert.match(html, /<form method="dialog"/);
});

test("freshCheckDialogHtml: escapes the age", () => {
  assert.ok(!freshCheckDialogHtml("<b>x</b>", escapeHtml).includes("<b>x</b>"));
});

test("checkButtonView: the naming phase says how many releases are named", () => {
  assert.deepEqual(
    checkButtonView({ ...idle, checking: true, status: "running", progress: 3, total: 25, phase: "naming" }),
    { label: "Naming 3 of 25", fill: 3 / 25, busy: true, spin: false },
  );
});

test("checkButtonView: a paused naming phase says Paused at", () => {
  const v = checkButtonView({ ...idle, checking: true, status: "paused", progress: 3, total: 25, phase: "naming" });
  assert.equal(v.label, "Paused at 3 of 25");
  assert.equal(v.fill, 3 / 25);
});

test("checkButtonView: the naming phase without a total still starts", () => {
  assert.equal(
    checkButtonView({ ...idle, checking: true, status: "running", phase: "naming" }).label,
    "Starting the check…",
  );
});
