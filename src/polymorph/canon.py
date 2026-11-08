"""Canonical, harness-neutral config."""

from __future__ import annotations

from dataclasses import dataclass

EVENTS = ("session_start", "pre_tool", "post_tool")
CLASSES = ("shell", "edit", "read", "mcp", "other")


@dataclass(frozen=True)
class Hook:
    event: str  # "session_start" | "pre_tool" | "post_tool"
    classes: tuple[str, ...]  # subset of CLASSES; () = all
    script: str  # path relative to repo root, under hooks_dir
    timeout: int | float | None = None
    reads: tuple[str, ...] = ()  # canonical payload fields declared in hook-reads.toml, beyond the class defaults


@dataclass(frozen=True)
class Canon:
    hooks: tuple[Hook, ...]
    unmapped: tuple[str, ...] = ()
