"""Check that a suggested name adds nothing that isn't in Civitai's own text.

Spelling may change (case, punctuation, run-together words split, ``version1.0``
to ``v1``, ``α`` to ``alpha``), but no new word or version number may appear.
This module only reports; it never changes a name.
"""

from __future__ import annotations

import re
from typing import List, Set

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


def _strip_zeros(number: str) -> str:
    """Drop trailing ``.0`` parts: ``4.0`` -> ``4``, ``1.0.0`` -> ``1``."""
    return re.sub(r"(\.0+)+$", "", number)


def _version_numbers(source: str) -> Set[str]:
    """Numbers that stand as a version in the source (see ``_VERSION_IN_SOURCE``)."""
    s = source.lower().replace("_", " ")
    return {_strip_zeros(m.group(1)) for m in _VERSION_IN_SOURCE.finditer(s)}


def invented_words(source: str, answer_parts: List[str]) -> List[str]:
    """Every word of the answer parts that is not traceable to ``source``."""
    compact_source = _compact(source)
    numbers = _version_numbers(source)
    invented = []
    for word in " ".join(answer_parts).split():
        version = _VERSION_WORD.fullmatch(word.lower())
        if version:
            if _strip_zeros(version.group(1)) not in numbers:
                invented.append(word)
            continue
        compact_word = _compact(word)
        if compact_word:
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
