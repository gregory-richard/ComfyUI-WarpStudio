// What the panel's floating status pill shows. It floats over the bottom of the
// panel, so it coming and going never moves the list. Pure: the panel applies it.
//
//   visible    whether the pill is out at all
//   text       its line: the job's text while one runs; else the cached-results
//              notice, an error, or a finished job's result while it flashes
//   level      "error" paints the line in the error colour
//   progress   the ring, the %, and the ETA (a job is running)
//   controls   Pause and Stop (a job is running)
//   noticeAct  Check again, next to the cached-results notice
//   dismiss    the × that clears an error
//
// The notice is newer than any error left over from an earlier job, so it wins.
export function statusRowView({ running, jobText, errorText, noticeText, flash }) {
  const error = errorText || "";
  const notice = noticeText || "";
  if (running) {
    return {
      visible: true,
      text: jobText || "",
      level: "info",
      progress: true,
      controls: true,
      noticeAct: false,
      dismiss: false,
    };
  }
  const text = notice || error || (flash ? jobText || "" : "");
  return {
    visible: Boolean(text),
    text,
    level: !notice && error ? "error" : "info",
    progress: false,
    controls: false,
    noticeAct: Boolean(notice),
    dismiss: !notice && Boolean(error),
  };
}
