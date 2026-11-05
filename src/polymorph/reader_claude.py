"""Read a repo's Claude Code config (.agents/settings.json) into a Canon."""

from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from .canon import Canon, Hook

HOOKS_DIR = ".agents/hooks"
SETTINGS = ".agents/settings.json"

EVENT_MAP = {
    "SessionStart": "session_start",
    "PreToolUse": "pre_tool",
    "PostToolUse": "post_tool",
}

_CLASS_RULES = (
    (re.compile(r"^Bash$"), "shell"),
    (re.compile(r"^(Edit|Write|MultiEdit)$"), "edit"),
    (re.compile(r"^(Read|Grep|Glob)$"), "read"),
    (re.compile(r"^mcp__"), "mcp"),
)
_CLASS_ORDER = ("shell", "edit", "read", "mcp", "other")

# The resolved script path is embedded in generated shell command lines, so it
# may contain nothing a shell would interpret (quotes, $, backticks, whitespace).
_SCRIPT_CHARS = re.compile(r"[A-Za-z0-9._+/-]+")


class ReaderError(Exception):
    pass


def classify_matcher(matcher: str | None) -> tuple[str, ...]:
    """Matcher regex -> classes. Missing/empty/'*' matcher = all = ()."""
    if matcher is None or matcher in ("", "*"):
        return ()
    found: set[str] = set()
    for part in matcher.split("|"):
        part = part.strip()
        for rx, cls in _CLASS_RULES:
            if rx.search(part):
                found.add(cls)
                break
        else:
            found.add("other")
    return tuple(c for c in _CLASS_ORDER if c in found)


def resolve_script(command: str, repo: Path) -> str:
    """Extract the script path from a hook command; refuse anything outside hooks dir."""
    repo = repo.resolve()
    expanded = command.replace("${CLAUDE_PROJECT_DIR}", str(repo)).replace(
        "$CLAUDE_PROJECT_DIR", str(repo)
    )
    try:
        tokens = shlex.split(expanded)
    except ValueError as e:
        raise ReaderError(f"unparseable hook command {command!r}: {e}") from e
    if len(tokens) != 1:
        raise ReaderError(
            f"hook command {command!r} is not a bare script path "
            "(arguments or interpreters are not portable)"
        )
    path = Path(tokens[0])
    if not path.is_absolute():
        path = repo / path
    real = Path(os.path.realpath(path))
    hooks_dir = Path(os.path.realpath(repo / HOOKS_DIR))
    if not real.is_relative_to(hooks_dir):
        raise ReaderError(f"hook script {command!r} resolves outside {HOOKS_DIR}/")
    script = str(real.relative_to(Path(os.path.realpath(repo))))
    if not _SCRIPT_CHARS.fullmatch(script):
        raise ReaderError(
            f"hook script {command!r}: script paths may contain only plain path characters "
            "(no quotes, whitespace, $, backticks or shell metacharacters)"
        )
    return script


def read_claude(repo: str | Path) -> Canon:
    repo = Path(repo)
    path = repo / SETTINGS
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError as e:
        raise ReaderError(f"{path} not found") from e
    except json.JSONDecodeError as e:
        raise ReaderError(f"{path}: invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ReaderError(f"{path}: top level must be an object")

    hooks: list[Hook] = []
    unmapped: list[str] = []
    for key, val in data.items():
        if key == "hooks":
            continue
        if key == "permissions" and isinstance(val, dict):
            for perm_key, items in val.items():
                count = len(items) if isinstance(items, list) else 1
                unmapped.append(f"permissions.{perm_key} ({count} entries)")
        else:
            unmapped.append(key)

    try:
        for event, groups in (data.get("hooks") or {}).items():
            canon_event = EVENT_MAP.get(event)
            if canon_event is None:
                unmapped.append(f"hooks.{event}")
                continue
            for group in groups:
                classes = () if canon_event == "session_start" else classify_matcher(group.get("matcher"))
                for hook_entry in group.get("hooks", []):
                    if hook_entry.get("type", "command") != "command":
                        unmapped.append(f"hooks.{event}[type={hook_entry.get('type')}]")
                        continue
                    script = resolve_script(hook_entry.get("command", ""), repo)
                    timeout = hook_entry.get("timeout")
                    if timeout is not None and (isinstance(timeout, bool) or int(timeout) <= 0):
                        raise ReaderError(f"hook {script}: timeout must be a positive number of seconds, got {timeout!r}")
                    hooks.append(Hook(canon_event, classes, script, int(timeout) if timeout is not None else None))
    except (AttributeError, TypeError, ValueError) as e:
        # a malformed entry is a settings problem, not a crash: check reports
        # settings-invalid and keeps going, generate exits 2 with the message
        raise ReaderError(f"malformed hooks entry: {e}") from e
    return Canon(tuple(hooks), tuple(unmapped))
