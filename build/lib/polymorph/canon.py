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
    timeout: int | None = None


@dataclass(frozen=True)
class Canon:
    hooks: tuple[Hook, ...]
    unmapped: tuple[str, ...] = ()
