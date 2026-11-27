"""When two paths name the same file: letter case counts on case-sensitive platforms.

Windows and macOS volumes ignore letter case by default, so ``Foo.safetensors``
and ``foo.safetensors`` are one file there; on Linux they are two. Paths and
stems used as a file's identity (set and dict keys, "is this name taken",
which files belong to a model) go through ``path_key``. Extensions, hashes,
type names and search text are not identities and keep ``.lower()``.

A case-insensitive volume mounted on Linux (an NTFS or exFAT model drive) still
reads as case-sensitive here: steps that rename onto a target also ask the disk
whether it is free, never this key alone.
"""

from __future__ import annotations

import os
import sys

# Read at call time (``path_key``), so tests can switch it.
CASE_SENSITIVE = sys.platform not in ("win32", "darwin")


def path_key(text: str) -> str:
    """The identity of a path or file name: lowercased where case does not count.

    Exactly ``text.lower()`` on Windows and macOS; never rewrites separators.
    """
    return text if CASE_SENSITIVE else text.lower()


def same_path(a: object, b: object) -> bool:
    """Whether two paths name one file, by text (``os.path.normpath`` and ``path_key``)."""
    return path_key(os.path.normpath(str(a))) == path_key(os.path.normpath(str(b)))
