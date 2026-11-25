// What the panel's floating status pill shows. It floats over the bottom of the
// panel, so it coming and going never moves the list. Pure: the panel applies it.
//
//   visible    whether the pill is out at all
//   text       its line: the job's text while one runs; else an error, or a
//              finished job's result while it flashes
//   level      "error" paints the line in the error colour
//   progress   the ring, the %, and the ETA (a job is running)
//   controls   Pause and Stop (a job is running)
//   dismiss    the × that clears an error
//
// ``onButton``: the job is a check and the Check for Updates button is in view. The
// button shows its progress and its result, so the pill only ever shows an error.
export function statusRowView({ running, jobText, errorText, flash, onButton }) {
  const error = errorText || "";
  if (running && !onButton) {
    return {
      visible: true,
      text: jobText || "",
      level: "info",
      progress: true,
      controls: true,
      dismiss: false,
    };
  }
  // Here a running job is a check on its button: only an error is the pill's.
  const text = error || (flash && !running && !onButton ? jobText || "" : "");
  return {
    visible: Boolean(text),
    text,
    level: text && text === error ? "error" : "info",
    progress: false,
    controls: false,
    dismiss: Boolean(text) && text === error,
  };
}
