// The Check for Updates button. While a check runs the button is its progress: it fills
// as files are checked, and the ⋯ square beside it is Stop. Pure: the panel applies it.
//
//   label  the button's text
//   fill   how much of it is filled (0..1) while a check with a known total runs, else null
//   busy   a check runs: a click does nothing
//   spin   the check has started but has no total yet
//   phase  "naming" while the releases found are being named, else the files are being checked
export function checkButtonView({ checking, status, progress, total, flashText, phase }) {
  if (!checking) {
    return { label: flashText || "Check for Updates", fill: null, busy: false, spin: false };
  }
  const done = Math.max(0, Number(progress || 0));
  const all = Math.max(0, Number(total || 0));
  if (all <= 0) return { label: "Starting the check…", fill: null, busy: true, spin: true };
  const doing = phase === "naming" ? "Naming" : "Checking";
  const label = status === "paused" ? `Paused at ${done} of ${all}` : `${doing} ${done} of ${all}`;
  return { label, fill: Math.min(1, done / all), busy: true, spin: false };
}

// The box that asks before checking again while the saved results are still fresh.
export function freshCheckDialogHtml(ago, escapeHtml) {
  return (
    `<form method="dialog" class="cu-dialog-body">` +
    `<p class="cu-dialog-text">These results are from ${escapeHtml(ago)}. Check every file against Civitai again?</p>` +
    `<div class="cu-dialog-actions">` +
    `<button type="submit" class="cu-btn cu-btn-sm cu-btn-primary" value="check">Check again</button>` +
    `<button type="submit" class="cu-btn cu-btn-sm cu-btn-ghost" value="keep">Keep these</button>` +
    `</div></form>`
  );
}
