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
