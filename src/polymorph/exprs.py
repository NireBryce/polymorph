"""Expressions for mappings: dotted-path lookup and {placeholder} substitution.

No eval, no str.format (which can reach attributes), no shell expansion. Values
substituted into structured output are JSON-encoded by the caller via json.dumps,
never pasted into a JSON string by hand.
"""
from __future__ import annotations

import re
from typing import Any

_MISSING = object()
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ExprError(ValueError):
    pass


def lookup(obj: Any, path: str, default: Any = _MISSING) -> Any:
    """Follow a dotted path through dicts (and list indexes). Raise ExprError or return default."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            if default is _MISSING:
                raise ExprError(f"path {path!r} not found at {part!r}")
            return default
    return cur


def assign(obj: dict, path: str, value: Any) -> None:
    """Set a dotted path in nested dicts, creating intermediate dicts."""
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = cur[part] = {}
        cur = nxt
    cur[parts[-1]] = value


def substitute(template: str, values: dict[str, Any]) -> str:
    """Replace {name} with str(values[name]). Unknown names are an error; no other syntax."""

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            raise ExprError(f"unknown placeholder {{{name}}}")
        v = values[name]
        return v if isinstance(v, str) else str(v)

    return _PLACEHOLDER.sub(repl, template)


def render(node: Any, values: dict[str, Any]) -> Any:
    """Substitute placeholders in every string of a nested structure (keys untouched).

    A string that is exactly one placeholder keeps the value's type, so structured
    values stay structured and the caller's json.dumps does the encoding.
    """
    if isinstance(node, str):
        m = _PLACEHOLDER.fullmatch(node)
        if m and m.group(1) in values:
            return values[m.group(1)]
        return substitute(node, values)
    if isinstance(node, dict):
        return {k: render(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [render(v, values) for v in node]
    return node
