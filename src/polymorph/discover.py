"""Discovery mode: log what an unknown harness sends to hooks, then draft a mapping.

The log is untrusted data. Nothing read from it is executed, evaluated or shelled out;
the draft only copies back values that match strict name patterns (tool names, key
names), never command text or paths.

Tool-name heuristics (case-insensitive, first match wins, checked in this order):
  shell: bash, shell, sh, terminal, command, exec, run_command, run_terminal_cmd
  edit:  edit, write, patch, replace, create, multiedit, str_replace, apply_patch
  read:  read, grep, glob, search, find, list, view, cat
Anything else (including mcp tools) is left out of [tools], not guessed.
"""

from __future__ import annotations

import json
import shlex
import os
import re
import sys
import time
from pathlib import Path


_TOOL_RE = re.compile(r"^[A-Za-z_][\w.*-]*$")
_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*$")
_EVENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

_HEURISTICS = (
    ("shell", ("bash", "shell", "terminal", "command", "exec", "sh")),
    ("edit", ("edit", "write", "patch", "replace", "create")),
    ("read", ("read", "grep", "glob", "search", "find", "list", "view", "cat")),
)
_TOOL_NAME_KEYS = ("tool_name", "toolName", "tool")
_INPUT_KEYS = ("tool_input", "toolInput", "input", "args", "arguments")
_COMMAND_KEYS = ("command", "cmd")
_PATH_KEYS = ("file_path", "filePath", "path")
_EVENT_KEYS = ("hook_event_name", "hookEventName", "event", "event_name")
_EVENT_CANON = (
    ("session_start", ("sessionstart", "session_start")),
    ("pre_tool", ("pretooluse", "pre_tool_use", "beforetool", "pre_tool")),
    ("post_tool", ("posttooluse", "post_tool_use", "aftertool", "post_tool")),
)


# ---- init -------------------------------------------------------------------

def log_command(harness: str, log_dir: str | Path | None = None) -> str:
    """Hook command: absolute interpreter and log path, so it works from any cwd."""
    out = Path(log_dir or Path.cwd() / ".polymorph-discovery").resolve() / f"{harness}.jsonl"
    return f"{shlex.quote(sys.executable)} -m polymorph discover log --out {shlex.quote(str(out))}"


def init_config(harness: str, config_path: str, event_name: str = "PreToolUse",
                matcher: str = "*", base: str | Path = ".", shape: str = "claude-settings",
                log_dir: str | Path | None = None) -> Path:
    """shape: claude-settings (hooks.<Event>, Claude Code and its SDK) or claude-like (ZCode)."""
    if shape not in ("claude-settings", "claude-like"):
        raise ValueError(f"unknown shape {shape!r}")
    if not re.match(r"^[a-z][a-z0-9_-]*$", harness):
        raise ValueError(f"harness name {harness!r} must match [a-z][a-z0-9_-]*")
    p = Path(config_path)
    if not p.is_absolute():
        p = Path(base) / p
    doc: dict = {}
    if p.is_file():
        doc = json.loads(p.read_text())
        if not isinstance(doc, dict):
            raise ValueError(f"{p}: existing config is not a JSON object")
    entry: dict = {"type": "command", "command": log_command(harness, log_dir)}
    events = {event_name: [{"matcher": matcher, "hooks": [entry]}]}
    if shape == "claude-like":
        entry["enabled"] = True
        doc["hooks"] = {"enabled": True, "events": events}
    else:
        existing = doc.get("hooks") if isinstance(doc.get("hooks"), dict) else {}
        doc["hooks"] = {**existing, **events}   # repeat calls add events
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n")
    return p


# ---- log --------------------------------------------------------------------

def _record(text: str) -> dict:
    try:
        raw = json.loads(text)
    except (ValueError, RecursionError):
        raw = text
    keys: list = []
    tool_input_keys: list = []
    if isinstance(raw, dict):
        keys = sorted(str(k) for k in raw)
        for k in _INPUT_KEYS:
            if isinstance(raw.get(k), dict):
                tool_input_keys = sorted(str(x) for x in raw[k])
                break
    return {
        "ts": time.time(),
        "env_keys": sorted(os.environ),   # names only, never values
        "raw": raw,
        "keys": keys,
        "tool_input_keys": tool_input_keys,
    }


def log_stdin(out: str, stream=None) -> None:
    """Append one record. Never raises, never writes stdout: must not affect the harness."""
    try:
        stream = stream or sys.stdin
        text = stream.read()
        line = json.dumps(_record(text), ensure_ascii=False)
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---- draft ------------------------------------------------------------------

def _first_key(d: dict, names) -> str | None:
    for n in names:
        if n in d:
            return n
    return None


def classify_tool(name: str) -> str | None:
    low = name.lower()
    if low.startswith("mcp"):
        return None
    for cls, words in _HEURISTICS:
        if any(w == low or low.startswith(w) or low.endswith(w) for w in words):
            return cls
    return None


def _load_log(path: str | Path) -> list[dict]:
    recs = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and isinstance(r.get("raw"), dict):
            recs.append(r["raw"])
    return recs


def draft_mapping(log: str | Path, name: str) -> str:
    if not re.match(r"^[a-z][a-z0-9_-]*$", name):
        raise ValueError(f"name {name!r} must match [a-z][a-z0-9_-]*")
    raws = _load_log(log)
    tools: dict[str, list[str]] = {}
    events: dict[str, str] = {}
    payload: dict[str, str] = {}

    def setp(k: str, v: str):
        payload.setdefault(k, v)

    for raw in raws:
        tk = _first_key(raw, _TOOL_NAME_KEYS)
        if tk and _KEY_RE.match(tk):
            setp("tool_name", tk)
            tn = raw[tk]
            if isinstance(tn, str) and _TOOL_RE.match(tn) and len(tn) <= 64:
                cls = classify_tool(tn)
                if cls and tn not in tools.setdefault(cls, []):
                    tools[cls].append(tn)
        ik = _first_key(raw, _INPUT_KEYS)
        if ik and isinstance(raw[ik], dict) and _KEY_RE.match(ik):
            ck = _first_key(raw[ik], _COMMAND_KEYS)
            if ck and _KEY_RE.match(ck):
                setp("shell.command", f"{ik}.{ck}")
            pk = _first_key(raw[ik], _PATH_KEYS)
            if pk and _KEY_RE.match(pk):
                setp("edit.file_path", f"{ik}.{pk}")
        for k in ("cwd", "session_id"):
            if k in raw:
                setp(k, k)
        ek = _first_key(raw, _EVENT_KEYS)
        if ek and isinstance(raw[ek], str) and _EVENT_RE.match(raw[ek]):
            low = raw[ek].lower()
            for canon, words in _EVENT_CANON:
                if low in words:
                    events.setdefault(canon, raw[ek])

    q = lambda s: json.dumps(s)
    out = [
        f"# DRAFT mapping for {name}, drafted from {len(raws)} observed payloads. UNVERIFIED.",
        "# Produced by `polymorph discover draft`. Tool classes come from name heuristics;",
        "# anything not observed is left out rather than guessed. No command text or paths",
        "# from the log are included. Review before use; `verified` is deliberately absent.",
        "schema = 1",
        f"name = {q(name)}",
        f'config_path = ".{name}/config.json"   # unknown: set to the real location',
        'format = "json"',
        'template = "claude-like"   # unknown: assumed from the discovery logger shape',
        "",
        "[tools]",
    ]
    for cls in ("shell", "edit", "read"):
        if cls in tools:
            out.append(f"{cls} = [{', '.join(q(t) for t in tools[cls])}]")
    out += ["", "[events]"]
    for canon, _ in _EVENT_CANON:
        if canon in events:
            out.append(f"{canon} = {q(events[canon])}")
    out += [
        "",
        "[capabilities]",
        "# Not observable from payloads; all false (conservative) until checked live.",
        "ask = false", "post_event = false", "exit_2_blocks = false",
        "rewrites_input = false", "json_answers = false",
        "",
        "[payload_in]",
    ]
    for k, v in payload.items():
        out.append(f"{q(k) if '.' in k else k} = {q(v)}")
    out += ["", "[payload_out]", ""]
    return "\n".join(out)
