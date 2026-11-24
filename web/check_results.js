// How the Updates and Issues cards word a check's results: the label and the
// reason of each saved file's row, and the status line when a check ends.
// Pure: nothing here touches ComfyUI or the page.

// The row label column fits "Downloaded", the longest label; the message line names the source.
const FALLBACK_LABEL = "Found";
const FALLBACK_SOURCES = {
  "ai-toolkit": { phrase: "your ai-toolkit training" },
  huggingface: { phrase: "Hugging Face" },
  civarchive: { phrase: "civarchive" },
};

const ISSUE_LABELS = {
  error: { role: "error", label: "Error" },
  not_found: { role: "nomatch", label: "No match" },
};

/**
 * The issue a saved file's row shows, or null. An archived file is never one:
 * its row says Archived. A copy on this drive of an archived release is: a
 * Duplicate, or Info only when the drive holds only its info file.
 */
export function rowIssue(v) {
  if (v.alsoArchived) {
    return v.infoOnly
      ? { role: "infoonly", label: "Info only" }
      : { role: "duplicate", label: "Duplicate" };
  }
  if (v.archived) return null;
  // A file another source identified is Found, not an issue; a failed lookup still is.
  if (v.status === "not_found" && FALLBACK_SOURCES[v.fallbackSource]) return null;
  return ISSUE_LABELS[v.status] || null;
}

/** The row's label and the role that colours its dot. A file another source identified says Found. */
export function rowLabel(v) {
  const issue = rowIssue(v);
  if (issue) return { role: issue.role, label: issue.label };
  if (!v.archived && v.status === "not_found" && FALLBACK_SOURCES[v.fallbackSource]) {
    return { role: "found", label: FALLBACK_LABEL };
  }
  // Downloaded since the check: the card stays in Updates until the next one.
  if (v.downloaded) return { role: "downloaded", label: "Downloaded" };
  if (v.archived) return { role: "archived", label: "Archived" };
  return v.metadataOnly
    ? { role: "metadata", label: "Metadata" }
    : { role: "saved", label: "Saved" };
}

/** The reason printed under an issue's row. */
export function issueMessage(v) {
  if (v.alsoArchived) {
    return v.infoOnly
      ? `Only the info file is on this drive. The model is in the archive as ${v.alsoArchived}.`
      : `The same version is in the archive as ${v.alsoArchived}.`;
  }
  const source = FALLBACK_SOURCES[v.fallbackSource];
  if (source && (v.status === "not_found" || v.status === "error")) {
    const found = `${v.hasLocalPreview ? "Info and preview" : "Info"} from ${source.phrase}.`;
    return v.status === "not_found"
      ? `Not on Civitai. ${found}`
      : `${v.error || "The lookup failed."} ${found}`;
  }
  if (v.status === "not_found") {
    return "Civitai has no release with this file's hash. It may be converted, merged, private, or never uploaded.";
  }
  return v.error || "The lookup failed.";
}

/**
 * What the status line says when a check ends, as [text, level] pairs for
 * setStatus: the count, then the archive's error when Check with archive
 * could not look at it, so it is not only in the ⓘ.
 */
export function checkDoneStatus(summary) {
  const updates = summary?.withUpdates || 0;
  const hidden = summary?.hiddenUpdates || 0;
  const lines = [
    [
      `Done — ${updates} update${updates !== 1 ? "s" : ""} found${hidden ? `, ${hidden} hidden` : ""}`,
      "info",
    ],
  ];
  if (summary?.archiveError) lines.push([String(summary.archiveError), "error"]);
  return lines;
}

/**
 * What the ⓘ knows about the archive after a check's summary, from `known`
 * ({ info, error }). A check that included the archive reports how it went:
 * its saved results (`archive`) and, when it could not look, `archiveError`
 * beside them. A plain check without saved results says nothing, so `known`
 * stays.
 */
export function archiveResult(summary, known) {
  if (!summary || !(summary.archive || summary.archiveError)) return known;
  return { info: summary.archive || known.info, error: String(summary.archiveError || "") };
}
