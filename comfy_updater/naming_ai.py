"""The naming AI's system prompt, the per-model message it gets, and its answer.

Code here only formats the message and reads the answer; it never rewrites names.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

PROMPT_PATH = Path(__file__).parent / "prompts" / "naming_system.txt"
SYSTEM_PROMPT: str = PROMPT_PATH.read_text(encoding="utf-8")
PROMPT_VERSION: str = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


@dataclass
class Slot:
    """One line of the versions list: a version, or one file of a multi-file version."""

    key: str
    version_id: str
    file_id: str
    version_name: str
    base_model: str
    file_name: str = ""  # only for versions with several model files


@dataclass
class ModelInput:
    model_id: str
    creator: str
    title: str
    description: str  # the model's description, HTML as Civitai serves it
    slots: list[Slot]
    new_bases: list[str]  # base models the code table does not know yet


@dataclass
class ModelAnswer:
    title: str
    original: str
    versions: dict[str, str]  # slot key -> version part
    bases: dict[str, str]  # new base model name (as in ModelInput.new_bases) -> code
    unparsed: bool


_TAG = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")


def description_excerpt(html_text: str, limit: int = 1500) -> str:
    text = _TAG.sub(" ", html_text or "")
    text = html.unescape(text)
    text = _SPACE.sub(" ", text).strip()
    return text[:limit].rstrip()


# Block-level tags end a line of the description; sentences split after . ! ?
_BLOCK_TAG = re.compile(r"<\s*/?\s*(?:br|p|li|h\d|div|ul|ol|blockquote|tr|hr)\b[^>]*>", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Words that show up where a description says who made the model. Common words
# that rarely mean credit (official, github, author, "originally") are left out.
_CREDIT_CUE = re.compile(
    r"\boriginal(?!ly)|credit|re-?upload|not (?:my|the) (?:own|model|creator|author)|i am not the|i'm not the"
    r"|convert|quantiz|model developer|developed by|made by|created by|trained by"
    r"|all rights|uploaded by|hugging ?face",
    re.IGNORECASE,
)


def _cut_words(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut after a whole word."""
    if len(text) <= limit:
        return text
    cut = text[: limit + 1].rsplit(" ", 1)[0] if " " in text[:limit + 1] else text[:limit]
    return cut[:limit].rstrip()


def credit_lines(html_text: str, limit: int = 600) -> str:
    """The description's sentences that may say who made the model, joined; "" if none.

    The AI sees only these, and only to answer the original maker. The rest of a
    description (trigger words, settings, changelogs with version numbers) is
    noise for naming, and the title and version never come from the description.
    Whole sentences are kept up to ``limit`` characters; a first sentence longer
    than that is cut after a whole word.
    """
    text = _BLOCK_TAG.sub("\n", html_text or "")
    text = html.unescape(_TAG.sub(" ", text))
    kept: list[str] = []
    seen: set[str] = set()
    size = 0
    for line in text.split("\n"):
        for sentence in _SENTENCE_END.split(_SPACE.sub(" ", line).strip()):
            sentence = sentence.strip()
            if not sentence or not _CREDIT_CUE.search(sentence) or sentence.lower() in seen:
                continue
            added = len(sentence) + (1 if kept else 0)
            if size + added > limit:
                return " ".join(kept) if kept else _cut_words(sentence, limit)
            seen.add(sentence.lower())
            kept.append(sentence)
            size += added
    return " ".join(kept)


def _plain(text: str) -> str:
    """Full-width and compatibility characters as their plain forms: "（pony）" -> "(pony)"."""
    return unicodedata.normalize("NFKC", text)


# Weight-precision words. The precision part comes from the file itself, never
# from a name, so version and file names reach the AI without them. Words that
# may also be real name words ("Q8 Turbo", "Scaled Down") only count in a form
# that is clearly a precision: q8_0 / q4_k_m, and scaled next to fp8.
_PRECISION = re.compile(
    r"(?<![a-z0-9])(?:fp8[_ -]?scaled|scaled[_ -]?fp8|fp8_e4m3fn|fp8_e5m2|e4m3fn|e5m2|nvfp4|mxfp8"
    r"|fp4|fp8|fp16|fp32|bf16|int8|int4|convrot|gguf|q[2-8]_(?:0|1|k(?:_[sml])?))(?![a-z0-9])",
    re.IGNORECASE,
)
_EMPTY_BRACKETS = re.compile(r"[(\[]\s*[\s+/&,_-]*[)\]]")
_JOINERS = re.compile(r"(?<=[\s_-])[+/&,](?=[\s_-])")
_DOT_RUN = re.compile(r"\.(?:[\s_-]*\.)+")
_SEPARATOR_RUN = re.compile(r"[\s_-]+")
_EXTENSION = re.compile(r"\.(?:safetensors|gguf|ckpt|pt|pth|bin)$", re.IGNORECASE)


def _one_separator(match: re.Match) -> str:
    """One separator for a run left where words were taken out."""
    run = match.group(0)
    if "_" in run:
        return "_"
    if "-" in run:
        # " - " only where the name itself had a spaced hyphen: "x-fp8-y" -> "x-y".
        return " - " if run[0] == " " and run[-1] == " " else "-"
    return " "


def without_precision(name: str) -> str:
    """A version or file name without its precision words: "krea2_raw_int8_convrot" -> "krea2_raw"."""
    extension = _EXTENSION.search(name)
    stem = name[: extension.start()] if extension else name
    text = _PRECISION.sub("", stem)
    if text == stem:
        return name
    text = _EMPTY_BRACKETS.sub(" ", _JOINERS.sub(" ", _EMPTY_BRACKETS.sub(" ", text)))
    text = _DOT_RUN.sub(".", text)  # "name.fp16.x" -> "name.x"
    text = _SEPARATOR_RUN.sub(_one_separator, text).strip(" _-.")
    return text + extension.group(0) if extension else text


def build_message(mi: ModelInput) -> str:
    lines = [
        f"Creator: {mi.creator}",
        f"Model title: {_plain(mi.title)}",
        "Versions:",
    ]
    for slot in mi.slots:
        name = without_precision(_plain(slot.version_name))
        line = f"{slot.key}. {name} [base: {slot.base_model}]" if name else f"{slot.key}. [base: {slot.base_model}]"
        if slot.file_name:
            line += f" [file: {without_precision(_plain(slot.file_name))}]"
        lines.append(line)
    if mi.new_bases:
        lines.append("New base models: " + ", ".join(mi.new_bases))
    lines.append(f"Credit lines (only for the original line): {credit_lines(mi.description) or 'none'}")
    return "\n".join(lines)


# Leads every grouped question. It lives here, not in the system prompt, so the
# system prompt (and with it PROMPT_VERSION and every cache key) does not change.
# The example models are made up.
GROUP_INTRO = """You get several models at once, each under its own header: "Model 1:", "Model 2:" and so on. Answer every model, in the same order, under a header with the same number, each with the usual lines. Name each model only from its own lines, never from another model's.

Example question:

Model 1:
Creator: fjordlamp
Model title: Brass Teapot Glow | SDXL
Versions:
1. v1.0 [base: SDXL 1.0]
Credit lines (only for the original line): none

Model 2:
Creator: mosslight_art
Model title: Paper Lantern Festival Krea2
Versions:
1. Krea2 Night v2.0 [base: Krea 2]
2. Dusk [base: Krea 2]
Credit lines (only for the original line): none

Model 3:
Creator: kiln_uploads
Model title: Ashwood Portraits [reupload]
Versions:
1. v3 [base: Illustrious]
Credit lines (only for the original line): Original model by Hollowpine, re-uploaded with permission.

Example answer:

Model 1:
title: brass teapot glow
original:
version 1: v1

Model 2:
title: paper lantern festival
original:
version 1: night v2
version 2: dusk

Model 3:
title: ashwood portraits
original: Hollowpine
version 1: v3

Now answer these models:"""


def build_group_message(inputs: list[ModelInput]) -> str:
    """Several models in one question: ``GROUP_INTRO``, then each one's own message under "Model 1:" ... "Model K:".

    One model is asked with its plain message, so its answer stays cacheable as is.
    """
    if len(inputs) == 1:
        return build_message(inputs[0])
    sections = "\n\n".join(f"Model {number}:\n{build_message(mi)}" for number, mi in enumerate(inputs, start=1))
    return GROUP_INTRO + "\n\n" + sections


def retry_message(mi: ModelInput, problems: list[str]) -> str:
    lines = [build_message(mi), "", "Your last answer had these problems:"]
    lines.extend(f"- {problem}" for problem in problems)
    lines.append("Answer again in the same format.")
    return "\n".join(lines)


_THINK_CLOSE = re.compile(r"</think>", re.IGNORECASE)
_THINK_TAG = re.compile(r"</?think>", re.IGNORECASE)
_MARKUP_LEAD = re.compile(r"^[\s*\-`#>]+")  # bullets, bold, headings, quotes, code fences
_KEY_END = r"[*`]*\s*:"  # "title:", "**title:**", "**title**:"
_TITLE_LINE = re.compile(r"^title" + _KEY_END + r"(.*)$", re.IGNORECASE)
_ORIGINAL_LINE = re.compile(r"^original" + _KEY_END + r"(.*)$", re.IGNORECASE)
_VERSION_LINE = re.compile(r"^version\s+(\S+?)" + _KEY_END + r"(.*)$", re.IGNORECASE)
_BASE_LINE = re.compile(r"^base\s+(.+?)" + _KEY_END + r"(.*)$", re.IGNORECASE)


# "Model 2:", "**Model 2:**", "### Model 2", "Model 2: title: x" (the rest of the line is the answer's).
_GROUP_HEADER = re.compile(r"^model\s+(\d+)\s*[*`]*\s*(?::|$)[*`]*\s*(.*)$", re.IGNORECASE)


def _without_thinking(text: str) -> str:
    closes = list(_THINK_CLOSE.finditer(text))
    if closes:
        text = text[closes[-1].end():]
    return _THINK_TAG.sub("", text)


def split_group_answer(text: str, count: int) -> list[str]:
    """The answer to a grouped question, one section of text per model ("" when it has none).

    Sections come from the "Model N:" headers. A header numbered outside 1..K means
    the numbering itself is off, so every model's answer is unparsed. Otherwise a
    section counts only when its number comes after the numbers before it and
    appears once; any other section belongs to nobody, so that model's answer is
    unparsed (a missing number affects only its own model).
    """
    sections: list[tuple[int, list[str]]] = []
    for raw in _without_thinking(text or "").splitlines():
        match = _GROUP_HEADER.match(_MARKUP_LEAD.sub("", raw).strip())
        if match:
            sections.append((int(match.group(1)), [match.group(2)] if match.group(2) else []))
        elif sections:
            sections[-1][1].append(raw)
    seen: dict[int, int] = {}
    for number, _lines in sections:
        seen[number] = seen.get(number, 0) + 1
    result = [""] * count
    if any(not 1 <= number <= count for number in seen):
        # Numbered some other way (from 0, past the group): every section may be on the wrong model.
        return result
    last = 0
    for number, lines in sections:
        if seen[number] > 1 or number <= last:
            continue
        last = number
        result[number - 1] = "\n".join(lines)
    return result


def parse_group_answer(text: str, inputs: list[ModelInput]) -> list[ModelAnswer]:
    """One ``ModelAnswer`` per model of a grouped question, in the group's order."""
    if len(inputs) == 1:
        return [parse_answer(text, inputs[0])]
    sections = split_group_answer(text, len(inputs))
    return [parse_answer(section, mi) for section, mi in zip(sections, inputs)]


def _value(raw: str) -> str:
    return raw.strip(" \t*`")


def parse_answer(text: str, mi: ModelInput) -> ModelAnswer:
    """Read the answer lines; prose around them is ignored. The first line of each kind wins.

    Everything up to the last ``</think>`` is thinking, not the answer. Markdown around
    the answer lines (bullets, bold keys, headings, code fences) is ignored.
    """
    text = _without_thinking(text or "")
    slot_keys = {slot.key for slot in mi.slots}
    base_names = {name.lower(): name for name in mi.new_bases}
    title = None
    original = None
    versions: dict[str, str] = {}
    bases: dict[str, str] = {}
    for raw in text.splitlines():
        line = _MARKUP_LEAD.sub("", raw).strip()
        match = _TITLE_LINE.match(line)
        if match:
            if title is None:
                title = _value(match.group(1))
            continue
        match = _ORIGINAL_LINE.match(line)
        if match:
            if original is None:
                original = _value(match.group(1))
            continue
        match = _VERSION_LINE.match(line)
        if match:
            key = match.group(1)
            if key in slot_keys and key not in versions:
                versions[key] = _value(match.group(2))
            continue
        match = _BASE_LINE.match(line)
        if match:
            name = base_names.get(match.group(1).strip().lower())
            if name is not None and name not in bases:
                bases[name] = _value(match.group(2))
    if title is None:
        return ModelAnswer(title="", original="", versions={}, bases={}, unparsed=True)
    return ModelAnswer(title=title, original=original or "", versions=versions, bases=bases, unparsed=False)
