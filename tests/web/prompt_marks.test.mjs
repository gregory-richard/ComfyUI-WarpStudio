import test from "node:test";
import assert from "node:assert/strict";
import {
  loraLines,
  loraState,
  cycleLora,
  hasEmbedding,
  addEmbedding,
} from "../../web/prompt_marks.js";

test("loraLines reads on and parked lines, skipping headers", () => {
  const text = "<lora:a:0.50>\n//// STYLE\n// <lora:b:0.80>";
  const got = loraLines(text);
  assert.deepEqual(
    got.map((l) => [l.name, l.state]),
    [
      ["a", "on"],
      ["b", "parked"],
    ]
  );
  assert.equal(text.slice(got[1].start, got[1].end), "// <lora:b:0.80>");
});

test("loraState prefers an on line over an earlier parked one", () => {
  const text = "// <lora:a:0.50>\n<lora:A:0.70>";
  assert.equal(loraState(text, "a"), "on");
  assert.equal(loraState("// <lora:a:0.50>", "a"), "parked");
  assert.equal(loraState("x", "a"), null);
});

test("cycleLora adds an absent LoRA at the end", () => {
  assert.deepEqual(cycleLora("", "maker - glow (krea2)"), {
    text: "<lora:maker - glow (krea2):1.00>",
    state: "on",
  });
  assert.equal(cycleLora("one", "a").text, "one\n<lora:a:1.00>");
  assert.equal(cycleLora("one\n", "a").text, "one\n<lora:a:1.00>");
});

test("cycleLora parks then restores, keeping the line", () => {
  const first = cycleLora("<lora:a:0.40>", "a");
  assert.deepEqual(first, { text: "// <lora:a:0.40>", state: "parked" });
  const second = cycleLora(first.text, "a");
  assert.deepEqual(second, { text: "<lora:a:0.40>", state: "on" });
});

test("cycleLora keeps indentation and only touches one line", () => {
  const text = "x\n  <lora:a:0.40> tail\n<lora:a:0.10>";
  assert.equal(cycleLora(text, "a").text, "x\n  // <lora:a:0.40> tail\n<lora:a:0.10>");
});

test("test_cycle_reads_current_text", () => {
  const a = cycleLora("<lora:a:0.40>", "a");
  const b = cycleLora("// <lora:a:0.90>\nfoo", "a");
  assert.equal(a.text, "// <lora:a:0.40>");
  assert.equal(b.text, "<lora:a:0.90>\nfoo");
});

test("names with regex characters match literally", () => {
  const stem = "maker - glow+ (v1.5) [x] (krea2)";
  assert.equal(loraState(`<lora:${stem}:1.00>`, stem), "on");
  assert.equal(loraState(`<lora:${stem.toUpperCase()}:1.00>`, stem), "on");
  assert.equal(loraState("<lora:maker - glow 2:1.00>", "maker - glow"), null);
  assert.equal(loraState("<lora:maker - glow:1.00>", "maker - glow 2"), null);
});

test("embeddings are added once, as whole tokens", () => {
  assert.equal(addEmbedding("a cat ", "fine"), "a cat, embedding:fine");
  assert.equal(addEmbedding("  ", "fine"), "embedding:fine");
  assert.equal(addEmbedding("a, embedding:fine", "fine"), "a, embedding:fine");
  assert.equal(hasEmbedding("embedding:fine2", "fine"), false);
  assert.equal(hasEmbedding("a, Embedding:FINE", "fine"), true);
});

test("cycleLora returns the state of the new text", () => {
  const text = "<lora:a:0.40>\n<lora:a:0.50>";
  const got = cycleLora(text, "a");
  assert.equal(got.text, "// <lora:a:0.40>\n<lora:a:0.50>");
  assert.equal(got.state, "on");
});

test("hasEmbedding finds weighted and sentence-final forms, not comments", () => {
  assert.equal(hasEmbedding("a, (embedding:fine:1.2)", "fine"), true);
  assert.equal(hasEmbedding("a cat with embedding:fine.", "fine"), true);
  assert.equal(hasEmbedding("embedding:fine.v2", "fine"), false);
  assert.equal(hasEmbedding("// embedding:fine\nx", "fine"), false);
  assert.equal(hasEmbedding("//// embedding:fine", "fine"), false);
});

test("addEmbedding avoids comment lines and double commas", () => {
  assert.equal(addEmbedding("a cat,", "fine"), "a cat, embedding:fine");
  assert.equal(addEmbedding("a cat\n// note", "fine"), "a cat\n// note\nembedding:fine");
  assert.equal(addEmbedding("a cat\r\n// note", "fine"), "a cat\r\n// note\r\nembedding:fine");
});

test("appending uses the text's own line ending", () => {
  assert.equal(cycleLora("one\r\ntwo", "a").text, "one\r\ntwo\r\n<lora:a:1.00>");
  assert.equal(cycleLora("one\r\n", "a").text, "one\r\n<lora:a:1.00>");
});

test("CRLF text cycles one line and keeps the line endings", () => {
  const text = "x\r\n<lora:a:0.40>\r\ny";
  const parked = cycleLora(text, "a");
  assert.equal(parked.text, "x\r\n// <lora:a:0.40>\r\ny");
  assert.equal(cycleLora(parked.text, "a").text, text);
  const [l] = loraLines(text);
  assert.equal(text.slice(l.start, l.end), "<lora:a:0.40>");
});

test("a /// parked line restores cleanly", () => {
  assert.equal(cycleLora("/// <lora:a:0.40>", "a").text, "<lora:a:0.40>");
});

test("an indented parked line restores with its indent", () => {
  assert.equal(cycleLora("x\n  // <lora:a:0.40>", "a").text, "x\n  <lora:a:0.40>");
});

test("weights of every shape and extra fields are recognised", () => {
  for (const w of ["1", "-0.40", ".5", "0.4:0.3"]) {
    assert.equal(loraState(`<lora:a:${w}>`, "a"), "on", w);
  }
});

test("loraLines offsets on a multi-line text", () => {
  const text = "one\n<lora:a:1>\n\n// <lora:b:.5>\nlast";
  const got = loraLines(text);
  assert.deepEqual(
    got.map((l) => text.slice(l.start, l.end)),
    ["<lora:a:1>", "// <lora:b:.5>"]
  );
});

test("undefined input never throws", () => {
  assert.deepEqual(loraLines(undefined), []);
  assert.equal(loraState(undefined, "a"), null);
  assert.equal(cycleLora(undefined, "a").text, "<lora:a:1.00>");
  assert.equal(hasEmbedding(undefined, "a"), false);
  assert.equal(addEmbedding(undefined, "a"), "embedding:a");
});

test("cycleLora leaves the text alone for a stem the tag cannot hold", () => {
  for (const stem of ["a:b", "a>b", "x:1.0>"]) {
    assert.deepEqual(cycleLora("one", stem), { text: "one", state: null });
    assert.deepEqual(cycleLora("", stem), { text: "", state: null });
  }
});
