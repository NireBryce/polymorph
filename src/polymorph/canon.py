"""Canonical, harness-neutral config."""

from __future__ import annotations

from dataclasses import dataclass

EVENTS = ("session_start", "pre_tool", "post_tool", "stop", "user_prompt_submit")
# events that fire for the session, not for a tool call: no tool class, no matcher
NON_TOOL_EVENTS = ("session_start", "stop", "user_prompt_submit")
CLASSES = ("shell", "edit", "read", "mcp", "other")


@dataclass(frozen=True)
class Hook:
    event: str  # one of EVENTS
    classes: tuple[str, ...]  # subset of CLASSES; () = all
    script: str  # path relative to repo root, under hooks_dir
    timeout: int | float | None = None
    reads: tuple[str, ...] = ()  # canonical payload fields declared in hook-reads.toml, beyond the class defaults


@dataclass(frozen=True)
class Canon:
    hooks: tuple[Hook, ...]
    unmapped: tuple[str, ...] = ()
