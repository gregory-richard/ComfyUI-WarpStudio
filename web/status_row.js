// What the panel's one status row shows. The row is always there at one height;
// this only says which parts of it are visible, so nothing ever moves the list.
//
//   text       the line to show: an error wins, else the job's own text
//   level      "error" paints the line in the error colour
//   progress   the progress track, %, and ETA (a job is running)
//   controls   Pause and Stop (a job is running)
//   noticeAct  the "Check again" button next to a cached-results notice
export function statusRowView({ running, jobText, errorText, noticeText }) {
  const error = errorText || "";
  return {
    text: error || jobText || "",
    level: error ? "error" : "info",
    progress: Boolean(running),
    controls: Boolean(running),
    noticeAct: !running && !error && Boolean(noticeText),
  };
}
