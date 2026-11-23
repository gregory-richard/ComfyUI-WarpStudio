import assert from "node:assert/strict";
import { test } from "node:test";

import {
  archiveResult,
  checkDoneStatus,
  issueMessage,
  rowIssue,
  rowLabel,
} from "../../web/check_results.js";

const row = (fields) => ({
  versionId: "7",
  status: "ok",
  metadataOnly: false,
  archived: false,
  fallbackSource: "",
  ...fields,
});

test("a drive copy of an archived release is a Duplicate", () => {
  const v = row({ alsoArchived: "copy.safetensors" });
  assert.deepEqual(rowLabel(v), { role: "duplicate", label: "Duplicate" });
  assert.ok(rowIssue(v));
  assert.equal(issueMessage(v), "The same version is in the archive as copy.safetensors.");
});

test("an info file alone on the drive for an archived release is Info only", () => {
  const v = row({ alsoArchived: "copy.safetensors", infoOnly: true, metadataOnly: true });
  assert.deepEqual(rowLabel(v), { role: "infoonly", label: "Info only" });
  assert.ok(rowIssue(v));
  assert.equal(
    issueMessage(v),
    "Only the info file is on this drive. The model is in the archive as copy.safetensors."
  );
});

test("an archived row is never an Error or No match: it says Archived", () => {
  for (const fields of [
    { status: "error", error: "500" },
    { status: "not_found" },
    { status: "not_found", fallbackSource: "civarchive" },
    { status: "ok" },
  ]) {
    const v = row({ archived: true, metadataOnly: true, ...fields });
    assert.equal(rowIssue(v), null, JSON.stringify(fields));
    assert.deepEqual(rowLabel(v), { role: "archived", label: "Archived" }, JSON.stringify(fields));
  }
});

test("rows on the drive keep their labels", () => {
  assert.deepEqual(rowLabel(row({ status: "error" })), { role: "error", label: "Error" });
  assert.deepEqual(rowLabel(row({ status: "not_found" })), { role: "nomatch", label: "No match" });
  assert.deepEqual(rowLabel(row({ status: "not_found", fallbackSource: "huggingface" })), {
    role: "nomatch",
    label: "Found",
  });
  assert.deepEqual(rowLabel(row({ metadataOnly: true })), { role: "metadata", label: "Metadata" });
  assert.deepEqual(rowLabel(row()), { role: "saved", label: "Saved" });
  assert.equal(rowIssue(row()), null);
  assert.equal(issueMessage(row({ status: "error", error: "Timed out" })), "Timed out");
  assert.equal(
    issueMessage(
      row({ status: "not_found", fallbackSource: "huggingface", hasLocalPreview: true })
    ),
    "Not on Civitai. Info and preview from Hugging Face."
  );
});

test("every row label fits the label column, which holds nine characters", () => {
  const rows = [
    row({ alsoArchived: "a" }),
    row({ alsoArchived: "a", infoOnly: true }),
    row({ archived: true }),
    row({ status: "error" }),
    row({ status: "not_found" }),
    row({ status: "not_found", fallbackSource: "civarchive" }),
    row({ metadataOnly: true }),
    row(),
  ];
  for (const v of rows) assert.ok(rowLabel(v).label.length <= 9, rowLabel(v).label);
});

test("a finished check says what it found", () => {
  assert.deepEqual(checkDoneStatus({ withUpdates: 1 }), [["Done — 1 update found", "info"]]);
  assert.deepEqual(checkDoneStatus({ withUpdates: 3, hiddenUpdates: 2 }), [
    ["Done — 3 updates found, 2 hidden", "info"],
  ]);
  assert.deepEqual(checkDoneStatus(null), [["Done — 0 updates found", "info"]]);
});

test("the ⓘ keeps an archive error beside the saved archive results", () => {
  const saved = { checkedAt: "2026-11-20T10:00:00Z", withUpdates: 12 };
  const known = { info: null, error: "" };
  // Unplugged, with results saved before: the summary carries both.
  const failed = archiveResult(
    { archive: saved, archiveError: "The archive folder can't be reached." },
    known
  );
  assert.deepEqual(failed, { info: saved, error: "The archive folder can't be reached." });
  // Unplugged, nothing saved yet.
  assert.deepEqual(
    archiveResult({ archiveError: "No model folders found in the archive folder." }, known),
    {
      info: null,
      error: "No model folders found in the archive folder.",
    }
  );
  // A check that looked at the archive, or a plain one with saved results: the error is over.
  assert.deepEqual(archiveResult({ archive: saved }, failed), { info: saved, error: "" });
  // A plain check with no saved results says nothing: what was known stays.
  assert.equal(archiveResult({ withUpdates: 3 }, failed), failed);
  assert.equal(archiveResult(null, failed), failed);
});

test("an archive that could not be checked is said inline as an error too", () => {
  for (const error of [
    "The archive folder can't be reached.",
    "No model folders found in the archive folder.",
    "The archive folder overlaps a model folder.",
  ]) {
    assert.deepEqual(checkDoneStatus({ withUpdates: 2, archiveError: error }), [
      ["Done — 2 updates found", "info"],
      [error, "error"],
    ]);
  }
});

test("rowLabel: a version downloaded since the check says Downloaded", () => {
  assert.deepEqual(rowLabel(row({ downloaded: true })), {
    role: "downloaded",
    label: "Downloaded",
  });
});

test("rowLabel: an issue still wins over Downloaded", () => {
  assert.deepEqual(rowLabel(row({ downloaded: true, alsoArchived: "x.safetensors" })), {
    role: "duplicate",
    label: "Duplicate",
  });
});
