"""Named built-in extractors. Selected only by name from a mapping; never loaded from it.

An extractor takes (native_payload) and returns {canonical dotted path: value}.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .exprs import lookup

_PATCH_FILE = re.compile(r"^\*\*\* (?:Update File|Add File|Delete File|Move to): (.+?)\s*$", re.M)
_PATCH_FIELDS = ("tool_input.input", "tool_input.patch", "tool_input.command", "tool_input.cmd")


class ExtractorError(ValueError):
    pass


def apply_patch_paths(native: dict) -> dict[str, Any]:
    """Pull file paths out of an apply_patch envelope into tool_input.file_path(s)."""
    for field in _PATCH_FIELDS:
        text = lookup(native, field, None)
        if isinstance(text, list):
            text = "\n".join(str(t) for t in text)
        if isinstance(text, str) and "*** " in text:
            paths = list(dict.fromkeys(_PATCH_FILE.findall(text)))
            if not paths:
                raise ExtractorError("apply_patch_paths: patch text has no file headers")
            return {"tool_input.file_path": paths[0], "tool_input.file_paths": paths}
    raise ExtractorError("apply_patch_paths: no patch text found in tool_input")


REGISTRY: dict[str, Callable[[dict], dict[str, Any]]] = {
    "apply_patch_paths": apply_patch_paths,
}


def get(name: str) -> Callable[[dict], dict[str, Any]]:
    if name not in REGISTRY:
        raise ExtractorError(f"unknown extractor {name!r} (known: {', '.join(sorted(REGISTRY))})")
    return REGISTRY[name]
