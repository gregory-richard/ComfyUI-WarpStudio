"""Checks on a suggested name; this module only reports, it never changes a name.

``invented_words``: nothing may be added that isn't in Civitai's own text. Spelling
may change (case, punctuation, run-together words split, ``version1.0`` to ``v1``,
``α`` to ``alpha``), but no new word or version number may appear.

``name_check_flags``: the base model belongs in the "(base)" part only, so a base
name left in the title or version is flagged, and so is a version written
``v1.0`` where the scheme wants ``v1``.
"""

from __future__ import annotations

import re
from typing import FrozenSet, Iterable, List, Set

from .base_models import _FAMILY_PREFIXES, base_family

GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ω": "omega"}

# A version number stands on its own: either right after v/ver/version (which may
# itself be glued to a word, as in "SDXLv2", and may be followed by a "." as in
# "v.3" or "ver. 2"), or with no letter, digit or "." in front. Nothing
# alphanumeric may follow, and no ".<digit>" either, so "1.5b" cannot backtrack
# into a "1".
_VERSION_IN_SOURCE = re.compile(
    r"(?:(?:v|ver|version)\s*\.?\s*|(?<![0-9a-z.]))(\d+(?:\.\d+)*)(?![0-9a-z]|\.\d)"
)
_VERSION_WORD = re.compile(r"v(\d+(?:\.\d+)*)")


def _compact(text: str) -> str:
    """Lowercase, Greek letters to their names, keep only [a-z0-9]."""
    text = text.lower()
    for letter, name in GREEK.items():
        text = text.replace(letter, name)
    return re.sub(r"[^a-z0-9]", "", text)


def strip_zeros(number: str) -> str:
    """Drop trailing ``.0`` parts: ``4.0`` -> ``4``, ``1.0.0`` -> ``1``."""
    return re.sub(r"(\.0+)+$", "", number)


def _version_numbers(source: str) -> Set[str]:
    """Numbers that stand as a version in the source (see ``_VERSION_IN_SOURCE``)."""
    s = source.lower().replace("_", " ")
    return {strip_zeros(m.group(1)) for m in _VERSION_IN_SOURCE.finditer(s)}


def invented_words(source: str, answer_parts: List[str]) -> List[str]:
    """Every word of the answer parts that is not traceable to ``source``."""
    compact_source = _compact(source)
    numbers = _version_numbers(source)
    invented = []
    for word in " ".join(answer_parts).split():
        version = _VERSION_WORD.fullmatch(word.lower())
        if version:
            if strip_zeros(version.group(1)) not in numbers:
                invented.append(word)
            continue
        compact_word = _compact(word)
        if compact_word:
            if compact_word in ("high", "low") and re.search(rf"(?<![a-z0-9]){compact_word[0]}n?(?![a-z0-9])", source.lower()):
                continue  # "H" / "L" (or "HN" / "LN") written out as high / low
            if compact_word not in compact_source:
                invented.append(word)
        elif any(ch.isalnum() for ch in word):
            # Letters outside a-z (e.g. "红潮") can't be traced, so they count as invented.
            invented.append(word)
    return invented


def maker_in_description(maker: str, description: str) -> bool:
    """True when the maker's name appears in the description (spelling may differ)."""
    compact_maker = _compact(maker)
    return bool(compact_maker) and compact_maker in _compact(description)


# --- Base-model names in the title or version --------------------------------

# Words that are part of base-model names but ordinary on their own: never a base
# word by themselves, though they don't stop an official title ("flux.1 dev").
GENERIC_BASE_WORDS = frozenset({
    "image", "base", "turbo", "video", "model", "models", "mini", "max", "lightning", "other", "dev",
    "schnell", "pro", "lite", "plus", "ultra", "large", "medium", "small", "open", "stable", "diffusion",
    "cascade", "aura", "playground", "flow", "edit", "fast", "distilled", "preview", "hyper",
})
# Base families whose models are SDXL-based, where a standalone "xl" names the base.
XL_FAMILIES = frozenset({"SDXL", "Pony", "Illustrious", "NoobAI"})

_PIECE_SPLIT = re.compile(r"[\s\-_/+()\[\],]+")
_WORD_PIECES = re.compile(r"[-_/+]")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
# A version or size number: "v2", "1.5", "14b". Never a base word.
_NUMBER_WORD = re.compile(r"v?\d+(?:\.\d+)*[a-z]?")
# What may follow or precede a base word glued to it: "ponyv6", "flux1d", "wan22".
_NUMBER_REST = re.compile(r"v?\d+[a-z]?\d*")
_TRAILING_ZERO = re.compile(r"v?\d+(?:\.\d+)*\.0+")
# Short model labels and precisions an official title may carry: "h3", "fp8", "bf16".
_OFFICIAL_TAG = re.compile(r"[a-z]{1,2}\d+|(?:fp|bf|int|nf)\d+")


def _vocab_word(word: str) -> bool:
    return len(word) >= 3 and word[0].isalpha() and word not in GENERIC_BASE_WORDS


# --- A version named for another base -------------------------------------------


def _words_of(*texts: str) -> Set[str]:
    """Base-like words of names and codes: compacted pieces and CamelCase parts, no generic words."""
    words: Set[str] = set()
    for text in texts:
        for piece in _PIECE_SPLIT.split(text or ""):
            compact = _compact(piece)
            if _vocab_word(compact):
                words.add(compact)
            if re.search(r"[a-z][A-Z]", piece):
                for part in _CAMEL.findall(piece):
                    part = part.lower()
                    if len(part) >= 4 and _vocab_word(part):
                        words.add(part)
    return words


def base_named_by(word: str, known_bases: dict, *, exclude: str = "") -> str:
    """The known base model a word names ("krea2" -> "Krea 2"), or "" when it names none or several.

    Base names that share one code (Krea 2, Krea 2 Turbo -> krea2) count as one.
    """
    matches = sorted(
        (name for name, code in known_bases.items()
         if isinstance(name, str) and name != exclude and word and word in _words_of(name, str(code or ""))),
        key=len,
    )
    codes = {known_bases[name] for name in matches}
    return matches[0] if len(codes) == 1 else ""


def base_conflict(version_name: str, base_model: str, known_bases: dict) -> str:
    """A word of the version's own name that names another base, or "".

    Civitai's base field is set by hand and is sometimes wrong: a version called
    "krea2 v1.0" listed as Flux.1 D. ``known_bases`` maps base-model names to
    codes (the base-code table). A word counts when it belongs to another base
    and not to this one, and the version name has no word of its own base that
    the other lacks ("ZIT and ZIB v2" listed as ZImageBase is fine).
    """
    if not version_name or not base_model:
        return ""
    written = _words_of(version_name)
    # A base word with a number glued on is still that base: "illustrious2", "pony6".
    words = written | {stem for stem in (re.sub(r"v?\d+[a-z]?\d*$", "", word) for word in written) if _vocab_word(stem)}
    if not words:
        return ""
    own = _words_of(base_model, str(known_bases.get(base_model) or ""))
    for other, code in known_bases.items():
        if not isinstance(other, str) or other == base_model:
            continue
        theirs = _words_of(other, str(code or ""))
        clash = sorted((word for word in words if word in theirs and word not in own), key=lambda w: (w not in written, w))
        if clash and not (words & (own - theirs)):
            return clash[0]  # as the name writes it, when it does
    return ""


def base_vocabulary(names: Iterable[str]) -> FrozenSet[str]:
    """Base words from base-model names and codes, plus the base families the updater knows.

    Each name is split into pieces (``Flux.2 Klein 9B`` -> ``flux2``, ``klein``) and a
    CamelCase piece also gives its distinctive parts (``NoobAI`` -> ``noob``). Words
    are compacted to [a-z0-9]; generic words, numbers and words under 3 letters are left out.
    """
    words: Set[str] = set()

    def add(text: str) -> None:
        for piece in _PIECE_SPLIT.split(text or ""):
            compact = _compact(piece)
            if _vocab_word(compact):
                words.add(compact)
            if re.search(r"[a-z][A-Z]", piece):
                for part in _CAMEL.findall(piece):
                    part = part.lower()
                    if len(part) >= 4 and _vocab_word(part):
                        words.add(part)

    for name in names:
        if isinstance(name, str):
            add(name)
    for prefix, family in _FAMILY_PREFIXES:
        for text in (prefix, family):
            compact = _compact(text)
            if _vocab_word(compact):
                words.add(compact)
    return frozenset(words)


def xl_is_base(base_model: str) -> bool:
    """True when the base model is SDXL-based, so a standalone "xl" names it."""
    return base_family(base_model) in XL_FAMILIES


def _glued_rest_ok(rest: str, base: str, vocabulary: FrozenSet[str]) -> bool:
    if _NUMBER_REST.fullmatch(rest):
        return True
    if len(base) < 4:
        return False
    return rest == "xl" or rest in vocabulary or rest in GENERIC_BASE_WORDS


def _is_base_word(word: str, vocabulary: FrozenSet[str], xl_base: bool) -> bool:
    """One word: a base name, its abbreviation, or one glued to a number or another base word."""
    if _NUMBER_WORD.fullmatch(word.lower()):
        return False
    compact = _compact(word)
    if not compact or compact in GENERIC_BASE_WORDS:
        return False
    if compact == "xl":
        return xl_base
    if compact in vocabulary:
        return True
    if len(compact) >= 4 and compact.isalpha():
        # An abbreviation: "illust" for illustrious.
        if any(len(base) > len(compact) and base.startswith(compact) for base in vocabulary):
            return True
    for base in vocabulary:
        if len(base) < 3 or len(base) >= len(compact):
            continue
        # Glued to something base-like only: "ponyxl", "flux1", but not "ponytail" or "animal".
        if compact.startswith(base) and _glued_rest_ok(compact[len(base):], base, vocabulary):
            return True
        if compact.endswith(base) and _glued_rest_ok(compact[: -len(base)], base, vocabulary):
            return True
    return False


def base_words(text: str, vocabulary: FrozenSet[str], *, xl_base: bool = False) -> List[str]:
    """The words of ``text`` that name a base model; a hyphenated word is checked whole, then by piece."""
    found: List[str] = []
    for word in text.split():
        if _is_base_word(word, vocabulary, xl_base):
            found.append(word)
            continue
        pieces = [piece for piece in _WORD_PIECES.split(word) if piece]
        if len(pieces) > 1:
            found.extend(piece for piece in pieces if _is_base_word(piece, vocabulary, xl_base))
    return list(dict.fromkeys(found))


def _official_word(word: str, vocabulary: FrozenSet[str], xl_base: bool) -> bool:
    if _is_base_word(word, vocabulary, xl_base) or _NUMBER_WORD.fullmatch(word.lower()):
        return True
    compact = _compact(word)
    if compact in GENERIC_BASE_WORDS or _OFFICIAL_TAG.fullmatch(compact):
        return True
    pieces = [piece for piece in _WORD_PIECES.split(word) if piece]
    return len(pieces) > 1 and all(_official_word(piece, vocabulary, xl_base) for piece in pieces)


def is_official_title(title: str, vocabulary: FrozenSet[str], *, xl_base: bool = False) -> bool:
    """Only base words, numbers and generic words, with at least one base word ("flux.1 dev")."""
    words = title.split()
    if not words or not all(_official_word(word, vocabulary, xl_base) for word in words):
        return False
    return bool(base_words(title, vocabulary, xl_base=xl_base))


def version_format(text: str) -> List[str]:
    """Version numbers with a trailing ``.0`` group (``v1.0``, ``10.0``); ``v1.5`` is fine."""
    return list(dict.fromkeys(word for word in text.split() if _TRAILING_ZERO.fullmatch(word.lower())))


def name_check_flags(
    title: str, version: str, vocabulary: FrozenSet[str], *, xl_base: bool = False, base_model: str = "",
    creator: str = "",
) -> List[str]:
    """``base-word:<word>`` and ``version-format:<token>`` codes for a title and version.

    An official model's title (only base words and numbers) keeps its base name;
    its version is still checked.
    """
    words: List[str] = []
    if not is_official_title(title, vocabulary, xl_base=xl_base):
        words.extend(base_words(title, vocabulary, xl_base=xl_base))
    words.extend(base_words(version, vocabulary, xl_base=xl_base))
    flags = [f"base-word:{word}" for word in words]
    flags.extend(f"version-format:{token}" for token in version_format(f"{title} {version}"))
    flags.extend(f"type-word:{word}" for word in type_words(f"{title} {version}"))
    flags.extend(f"base-number:{token}" for token in base_numbers(version, base_model))
    if not is_official_title(title, vocabulary, xl_base=xl_base):
        # An official model's name may carry its maker's ("qwen image 2.1").
        flags.extend(f"creator-in-title:{word}" for word in creator_in_title(title, creator))
    flags.extend(f"title-in-version:{phrase}" for phrase in title_in_version(title, version))
    flags.extend(f"noise-word:{word}" for word in noise_words(version, base_model))
    return list(dict.fromkeys(flags))


def creator_in_title(title: str, creator: str) -> List[str]:
    """The creator's name standing as a word of the title ("xlabs - xlabs flux realism")."""
    name = _compact(creator or "")
    words = (title or "").split()
    if len(name) < 3 or _compact(title or "") == name:
        return []  # a model named after its creator ("donutsdelivery")
    # Also written as several words: "stable yogi" for stable_yogi.
    for size in (1, 2, 3):
        for start in range(len(words) - size + 1):
            if _compact("".join(words[start:start + size])) == name:
                return [" ".join(words[start:start + size])]
    return []


def title_in_version(title: str, version: str) -> List[str]:
    """Two or more words of the title repeated in a row in the version ("amateur selfies")."""
    title_words = (title or "").lower().split()
    version_words = (version or "").lower().split()
    found = []
    for size in range(min(len(title_words), len(version_words)), 1, -1):
        for start in range(len(version_words) - size + 1):
            phrase = version_words[start:start + size]
            if any(title_words[i:i + size] == phrase for i in range(len(title_words) - size + 1)):
                return [" ".join(phrase)]
    return found


# Wan 2.2 ships every LoRA as a high-noise and a low-noise file: the names say "high" and "low".
_NOISE = re.compile(r"\b(high|low)[ _-]?noise\b")


def noise_words(version: str, base_model: str = "") -> List[str]:
    """"high noise" / "highnoise" (and low) in a version, which the names write as "high" / "low".

    On a Wan model a lone "h" / "l" (or "hn" / "ln") is the same thing, shortened.
    """
    text = (version or "").lower()
    found = [match.group(0) for match in _NOISE.finditer(text)]
    if "wan" in (base_model or "").lower():
        found.extend(token for token in text.split() if token in ("h", "l", "hn", "ln"))
    return list(dict.fromkeys(found))


def glued_names(source_title: str, answer_title: str, vocabulary: FrozenSet[str]) -> List[str]:
    """Words of the Civitai title that glue a base name onto another word ("KreaCore") and that the
    answer's title no longer holds whole.

    Such a word is the creator's own name for the model, like "CleavageXL": the
    base name in it is part of the name, not a base tag to take out.
    """
    answer = _compact(answer_title or "")
    found = []
    for piece in _PIECE_SPLIT.split(source_title or ""):
        word = _compact(piece)
        if len(word) < 6 or word in vocabulary or word in answer:
            continue
        for base in vocabulary:
            if len(base) < 4 or base == word:
                continue
            rest = word[len(base):] if word.startswith(base) else word[: -len(base)] if word.endswith(base) else ""
            if len(rest) >= 3 and rest.isalpha() and rest not in vocabulary:
                found.append(piece)
                break
    return found


def base_numbers(version: str, base_model: str) -> List[str]:
    """Version numbers that are only the base model's own number: "v2.2" on Wan Video 2.2.

    Only numbers with a dot count (2.2, 1.5, 2.3), and not x.0: a plain "v2" on
    Krea 2 or "v1.0" on SDXL 1.0 is far more often a real version.
    """
    # "SDXL 1.0": an x.0 is as common in versions as it is in base names; it tells nothing.
    own = {number for number in re.findall(r"\d+\.\d+", base_model or "") if not re.fullmatch(r"\d+\.0+", number)}
    if not own:
        return []
    found = []
    for token in (version or "").lower().split():
        match = re.fullmatch(r"v?(\d+\.\d+)", token)
        if match and match.group(1) in own and token not in found:
            found.append(token)
    return found


# What kind of file it is, never part of its name (the naming rules say to drop them).
TYPE_WORDS = frozenset({"lora", "loras", "lycoris", "locon", "loha", "lokr"})


def type_words(text: str) -> List[str]:
    """Words like "lora" standing on their own in a name."""
    return list(dict.fromkeys(word for word in re.findall(r"[a-z0-9]+", text.lower()) if word in TYPE_WORDS))
