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

import base64
import hashlib
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

def log_command(harness: str, log_dir: str | Path | None = None, redact: bool = True,
                out: str | Path | None = None, env: bool = False) -> str:
    """Hook command: absolute interpreter and log path, so it works from any cwd."""
    if out is None:
        out = Path(log_dir or Path.cwd() / ".polymorph-discovery").resolve() / f"{harness}.jsonl"
    flag = ("" if redact else " --full") + (" --env-be-very-careful" if env else "")
    return f"{shlex.quote(sys.executable)} -m polymorph discover log{flag} --out {shlex.quote(str(Path(out).resolve()))}"


def _check_harness(harness: str) -> None:
    if not re.match(r"^[a-z][a-z0-9_-]*$", harness):
        raise ValueError(f"harness name {harness!r} must match [a-z][a-z0-9_-]*")


def _resolve(config_path: str, base: str | Path) -> Path:
    p = Path(config_path)
    return p if p.is_absolute() else Path(base) / p


def _load_doc(p: Path) -> dict:
    doc: dict = {}
    if p.is_file():
        doc = json.loads(p.read_text())
        if not isinstance(doc, dict):
            raise ValueError(f"{p}: existing config is not a JSON object")
    return doc


def _merge_hook(doc: dict, shape: str, event_name: str, matcher: str, entry: dict,
                append: bool = False) -> None:
    """The one merge: add a catch-all hook entry under event_name.

    append=False replaces that event's list (init: repeat calls add events);
    append=True adds a matcher group beside whatever the event already has.
    """
    group = {"matcher": matcher, "hooks": [entry]}
    if shape == "claude-like":
        entry["enabled"] = True
        hooks = doc.get("hooks") if isinstance(doc.get("hooks"), dict) else {}
        evs = hooks.get("events") if isinstance(hooks.get("events"), dict) else {}
        old = evs.get(event_name) if append and isinstance(evs.get(event_name), list) else []
        doc["hooks"] = {**hooks, "enabled": True, "events": {**evs, event_name: [*old, group]}}
    else:
        hooks = doc.get("hooks") if isinstance(doc.get("hooks"), dict) else {}
        old = hooks.get(event_name) if append and isinstance(hooks.get(event_name), list) else []
        doc["hooks"] = {**hooks, event_name: [*old, group]}


def _write_doc(p: Path, doc: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2) + "\n")


def init_config(harness: str, config_path: str, event_name: str = "PreToolUse",
                matcher: str = "*", base: str | Path = ".", shape: str = "claude-settings",
                log_dir: str | Path | None = None, redact: bool = True, env: bool = False) -> Path:
    """shape: claude-settings (hooks.<Event>, Claude Code and its SDK) or claude-like (ZCode)."""
    if shape not in ("claude-settings", "claude-like"):
        raise ValueError(f"unknown shape {shape!r}")
    _check_harness(harness)
    p = _resolve(config_path, base)
    doc = _load_doc(p)
    entry: dict = {"type": "command", "command": log_command(harness, log_dir, redact, env=env)}
    if shape == "claude-like":
        # replaces the whole hooks table, as it always has
        doc["hooks"] = {"enabled": True,
                        "events": {event_name: [{"matcher": matcher, "hooks": [{**entry, "enabled": True}]}]}}
    else:
        _merge_hook(doc, shape, event_name, matcher, entry)
    _write_doc(p, doc)
    return p


# ---- reload-check -----------------------------------------------------------

def _state_path(scratch: Path) -> Path:
    return scratch.with_name(scratch.name + ".reload-state.json")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _detect_shape(doc: dict) -> str:
    h = doc.get("hooks")
    return "claude-like" if isinstance(h, dict) and isinstance(h.get("events"), dict) else "claude-settings"


def reload_check_start(config_path: str, scratch_log: str, event_name: str = "PreToolUse",
                       matcher: str = "*", base: str | Path = ".", shape: str | None = None) -> str:
    """Add a temporary extra hook (distinct out file); return instructions for the user."""
    p = _resolve(config_path, base).resolve()
    scratch = Path(scratch_log).resolve()
    st = _state_path(scratch)
    if st.exists():
        raise ValueError(f"{st} exists: a reload check is already open; run it with --finish first")
    original = p.read_bytes() if p.is_file() else None
    doc = _load_doc(p)
    entry = {"type": "command", "command": log_command("reload-check", out=scratch)}
    _merge_hook(doc, shape or _detect_shape(doc), event_name, matcher, entry, append=True)
    # state first: if the config write fails midway, finish can still clean up
    scratch.parent.mkdir(parents=True, exist_ok=True)
    state = {"config": str(p), "existed": original is not None,
             "original_b64": base64.b64encode(original or b"").decode()}
    st.write_text(json.dumps(state))
    try:
        _write_doc(p, doc)
    except Exception:
        _restore(p, state)
        st.unlink(missing_ok=True)
        raise
    state["written_sha256"] = _sha(p.read_bytes())
    st.write_text(json.dumps(state))
    return "\n".join([
        f"Temporary hook added to {p} ({event_name}), logging to {scratch}.",
        "Do NOT restart the harness or reload its config. In the already-running session:",
        "  1. have the agent make exactly one tool call (e.g. run `true` with the shell tool),",
        "  2. then run:",
        f"     python -m polymorph discover reload-check --finish --config-path {shlex.quote(str(p))}"
        f" --scratch-log {shlex.quote(str(scratch))}",
        "The finish step reports whether the running harness picked up the edit and removes the hook.",
    ])


def _restore(p: Path, state: dict) -> None:
    if state["existed"]:
        p.write_bytes(base64.b64decode(state["original_b64"]))
    else:
        p.unlink(missing_ok=True)


def _strip_marker(doc: dict, marker: str) -> int:
    n = 0

    def groups(lst):
        nonlocal n
        out = []
        for g in lst:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                keep = [h for h in g["hooks"] if not (isinstance(h, dict) and marker in str(h.get("command", "")))]
                n += len(g["hooks"]) - len(keep)
                if not keep:
                    continue
                g = {**g, "hooks": keep}
            out.append(g)
        return out

    h = doc.get("hooks")
    if isinstance(h, dict):
        tables = [h] + ([h["events"]] if isinstance(h.get("events"), dict) else [])
        for t in tables:
            for k, v in list(t.items()):
                if isinstance(v, list):
                    t[k] = groups(v)
    return n


def reload_check_finish(config_path: str, scratch_log: str, base: str | Path = ".") -> tuple[bool, str]:
    """Remove the temporary hook and report whether the scratch log got a line."""
    p = _resolve(config_path, base).resolve()
    scratch = Path(scratch_log).resolve()
    st = _state_path(scratch)
    seen = scratch.is_file() and any(l.strip() for l in scratch.read_text(errors="replace").splitlines())
    notes = []
    if st.is_file():
        state = json.loads(st.read_text())
        cur = p.read_bytes() if p.is_file() else None
        if cur is not None and _sha(cur) == state.get("written_sha256"):
            _restore(p, state)
            notes.append(f"{p} restored byte-for-byte")
        else:
            doc = _load_doc(p)
            removed = _strip_marker(doc, str(scratch))
            _write_doc(p, doc)
            notes.append(f"{p} changed since the check began; removed {removed} temporary hook(s) "
                         "by marker instead of restoring")
        st.unlink(missing_ok=True)
    else:
        doc = _load_doc(p)
        removed = _strip_marker(doc, str(scratch))
        if removed:
            _write_doc(p, doc)
        notes.append(f"no state file; removed {removed} temporary hook(s) by marker")
    verdict = ("RELOADED: the running harness picked up the config edit without a restart"
               if seen else
               "NOT SEEN: scratch log is empty. Either the harness needs a restart/reload to read hook "
               "edits, or no tool call matched the hook")
    return seen, "\n".join([verdict, *notes])


# ---- log --------------------------------------------------------------------

def _first_str(raw: dict, names) -> str | None:
    for n in names:
        if isinstance(raw.get(n), str):
            return raw[n]
    return None


_SESSION_KEYS = ("session_id", "sessionId")
_RESPONSE_KEYS = ("tool_response", "toolResponse")
_JSON_TYPES = ((bool, "boolean"), (int, "number"), (float, "number"), (str, "string"),
               (list, "array"), (dict, "object"), (type(None), "null"))


# Values kept verbatim under redaction: structure, never user content. Everything
# else (cwd, transcript_path, prompts, tool_input, tool_response, ...) is reduced
# to {type, length}.
_SAFE_VALUES = ("hook_event_name", "hookEventName", "event", "tool_name", "toolName",
                "session_id", "sessionId", "tool_use_id", "prompt_id",
                "permission_mode", "effort", "source", "duration_ms")


def _descriptor(v) -> dict:
    typ = next(n for t, n in _JSON_TYPES if isinstance(v, t))
    data = v if isinstance(v, str) else json.dumps(v, sort_keys=True, ensure_ascii=False)
    return {"type": typ, "length": len(data)}


def _redact_payload(raw):
    """Keys kept; only _SAFE_VALUES survive verbatim, the rest become {type, length}."""
    if not isinstance(raw, dict):
        return _descriptor(raw)
    out = {}
    for k, v in raw.items():
        if k in _SAFE_VALUES and isinstance(v, (str, int, float, bool)) and not isinstance(v, dict):
            out[k] = v
        elif k in (*_INPUT_KEYS, *_RESPONSE_KEYS) and isinstance(v, dict):
            out[k] = {kk: _descriptor(vv) for kk, vv in v.items()}
        else:
            out[k] = _descriptor(v)
    return out


# Only with --env-be-very-careful: names (never values) of variables that say where the project is.
_ENV_NAME = re.compile(r"PROJECT|WORKSPACE|HARNESS", re.I)


def _env_keys() -> list[str]:
    return sorted(k for k in os.environ if _ENV_NAME.search(k))


def _record(text: str, redact: bool = True, env: bool = False) -> dict:
    try:
        raw = json.loads(text)
    except (ValueError, RecursionError):
        raw = text
    keys: list = []
    tool_input_keys: list = []
    event = tool_name = session_id = None
    if isinstance(raw, dict):
        keys = sorted(str(k) for k in raw)
        for k in _INPUT_KEYS:
            if isinstance(raw.get(k), dict):
                tool_input_keys = sorted(str(x) for x in raw[k])
                break
        event = _first_str(raw, _EVENT_KEYS)
        tool_name = _first_str(raw, _TOOL_NAME_KEYS)
        session_id = _first_str(raw, _SESSION_KEYS)
    return {
        "ts": time.time(),
        "event": event,
        "tool_name": tool_name,
        "session_id": session_id,
        "env_keys": _env_keys() if env else [],   # opt-in; names only, never values
        "redacted": redact,
        "raw": _redact_payload(raw) if redact else raw,
        "keys": keys,
        "tool_input_keys": tool_input_keys,
    }


def _log_error(out: str, what: str) -> None:
    """Sidecar <name>.errors.jsonl beside the log; best effort, never raises."""
    try:
        p = Path(out)
        side = p.with_name(p.stem + ".errors.jsonl")
        side.parent.mkdir(parents=True, exist_ok=True)
        with open(side, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "error": what}) + "\n")
    except Exception:
        pass


def log_stdin(out: str, stream=None, redact: bool = True, env: bool = False) -> None:
    """Append one record. Never raises, never writes stdout: must not affect the harness."""
    try:
        stream = stream or sys.stdin
        text = stream.read()
        rec = _record(text, redact, env)
        try:
            is_obj = isinstance(json.loads(text), dict)
            why = "stdin JSON is not an object"
        except (ValueError, RecursionError):
            is_obj, why = False, "stdin is not JSON"
        if not is_obj:
            _log_error(out, f"{why} ({len(text)} chars)")
        line = json.dumps(rec, ensure_ascii=False)
        p = Path(out)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        _log_error(out, f"{type(e).__name__}: {e}")


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


def _load_records(path: str | Path) -> tuple[list[dict], int]:
    """Log lines whose raw payload is an object, plus the count of other lines."""
    recs, other = [], 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        if isinstance(r.get("raw"), dict):
            recs.append(r)
        else:
            other += 1
    return recs, other


def _top(rec: dict) -> tuple[str | None, str | None, str | None]:
    """event, tool_name, session_id from the line's top level (older logs: derived once)."""
    if "event" in rec or "tool_name" in rec:
        return rec.get("event"), rec.get("tool_name"), rec.get("session_id")
    raw = rec["raw"]
    return (_first_str(raw, _EVENT_KEYS), _first_str(raw, _TOOL_NAME_KEYS),
            _first_str(raw, _SESSION_KEYS))


def _safe(s, pat=_TOOL_RE) -> str | None:
    return s if isinstance(s, str) and len(s) <= 64 and pat.match(s) else None


# read-class tool_input keys worth mapping when seen: canonical field -> tool_input key names
_READ_FIELDS = (("read.file_path", ("file_path", "filePath")), ("read.path", ("path",)),
                ("read.pattern", ("pattern",)), ("read.glob", ("glob",)))


def draft_mapping(log: str | Path, name: str) -> str:
    if not re.match(r"^[a-z][a-z0-9_-]*$", name):
        raise ValueError(f"name {name!r} must match [a-z][a-z0-9_-]*")
    recs, _ = _load_records(log)
    total = len(recs)
    tools: dict[str, list[str]] = {}
    tool_calls: dict[str, int] = {}        # safe tool name -> payloads
    skipped: dict[str, str] = {}           # safe tool name -> reason
    unnamed_bad = 0
    events: dict[str, str] = {}
    event_counts: dict[str, int] = {}
    payload: dict[str, str] = {}
    # canonical key -> {tool or "*": [present, seen]}
    evidence: dict[str, dict[str, int]] = {}
    basic: dict[str, int] = {}
    claude_style = False

    def setp(k: str, v: str):
        payload.setdefault(k, v)

    def count(k: str, tool: str):
        evidence.setdefault(k, {})
        evidence[k][tool] = evidence[k].get(tool, 0) + 1

    for rec in recs:
        raw = rec["raw"]
        ev, tn, sid = _top(rec)
        tool = _safe(tn)
        if tn is not None and tool is None:
            unnamed_bad += 1
        tk = _first_key(raw, _TOOL_NAME_KEYS)
        if tk and _KEY_RE.match(tk) and tn is not None:
            setp("tool_name", tk)
            basic["tool_name"] = basic.get("tool_name", 0) + 1
        cls = None
        if tool and len(tool) <= 64:
            tool_calls[tool] = tool_calls.get(tool, 0) + 1
            cls = classify_tool(tool)
            if cls:
                if tool not in tools.setdefault(cls, []):
                    tools[cls].append(tool)
            else:
                skipped[tool] = ("mcp tool: not classified, no name heuristic applies"
                                 if tool.lower().startswith("mcp")
                                 else "no name heuristic matched")
        ik = _first_key(raw, _INPUT_KEYS)
        if cls and ik and isinstance(raw[ik], dict) and _KEY_RE.match(ik):
            inp = raw[ik]
            if cls == "shell":
                ck = _first_key(inp, _COMMAND_KEYS)
                if ck and _KEY_RE.match(ck):
                    setp("shell.command", f"{ik}.{ck}")
                    count("shell.command", tool)
            elif cls == "edit":
                pk = _first_key(inp, _PATH_KEYS)
                if pk and _KEY_RE.match(pk):
                    setp("edit.file_path", f"{ik}.{pk}")
                    count("edit.file_path", tool)
            else:
                for canon, names in _READ_FIELDS:
                    kk = _first_key(inp, names)
                    if kk and _KEY_RE.match(kk):
                        setp(canon, f"{ik}.{kk}")
                        count(canon, tool)
        if "cwd" in raw:
            setp("cwd", "cwd")
            basic["cwd"] = basic.get("cwd", 0) + 1
        if sid is not None:
            setp("session_id", "session_id" if "session_id" in raw else
                 _first_key(raw, _SESSION_KEYS) or "session_id")
            basic["session_id"] = basic.get("session_id", 0) + 1
        if ev is not None and _EVENT_RE.match(ev):
            low = ev.lower()
            for canon, words in _EVENT_CANON:
                if low in words:
                    events.setdefault(canon, ev)
                    event_counts[canon] = event_counts.get(canon, 0) + 1
        if "hook_event_name" in raw and "cwd" in raw:
            claude_style = True

    def ev_str(key: str) -> str:
        if key in basic:
            return f"{basic[key]}/{total} payloads"
        parts = []
        for t, n in sorted(evidence.get(key, {}).items()):
            parts.append(f"{n}/{tool_calls.get(t, n)} {t}")
        return ", ".join(parts)

    q = lambda s: json.dumps(s)
    out = [
        f"# DRAFT mapping for {name}, drafted from {total} observed payloads. UNVERIFIED.",
        "# Produced by `polymorph discover draft`. Tool classes come from name heuristics;",
        "# anything not observed is left out rather than guessed. No command text or paths",
        "# from the log are included. Review before use; `verified` is deliberately absent.",
        "# Counts in comments are present/observed over the payloads of that tool.",
        "schema = 1",
        f"name = {q(name)}",
    ]
    if claude_style:
        out += [
            'config_path = ".claude/settings.json"   # inferred: log has cwd + hook_event_name (Claude-style); confirm',
            'format = "json"',
            'template = "claude-settings"   # inferred from the same evidence; confirm',
        ]
    else:
        out += [
            f'config_path = ".{name}/config.json"   # unknown: set to the real location',
            'format = "json"',
            'template = "claude-like"   # unknown: assumed from the discovery logger shape',
        ]
    out += ["", "[tools]"]
    for cls in ("shell", "edit", "read"):
        if cls in tools:
            n = sum(tool_calls.get(t, 0) for t in tools[cls])
            out.append(f"{cls} = [{', '.join(q(t) for t in tools[cls])}]   # guessed from name ({n} calls)")
    if skipped or unnamed_bad:
        out.append("# Not classified (left out, not guessed):")
        for t, why in sorted(skipped.items()):
            out.append(f"#   {t} ({tool_calls[t]} calls): {why}")
        if unnamed_bad:
            out.append(f"#   {unnamed_bad} payload(s) with a tool name that fails the name pattern: ignored")
    out += ["", "[events]"]
    for canon, _ in _EVENT_CANON:
        if canon in events:
            out.append(f"{canon} = {q(events[canon])}   # {event_counts[canon]}/{total} payloads")
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
        e = ev_str(k)
        out.append(f"{q(k) if '.' in k else k} = {q(v)}" + (f"   # {e}" if e else ""))
    out += ["", "[payload_out]", ""]
    return "\n".join(out)


# ---- summary ----------------------------------------------------------------

def summarize(log: str | Path) -> str:
    recs, other = _load_records(log)
    by_event: dict[str, dict] = {}
    by_tool: dict[str, dict] = {}
    sessions: set = set()

    def bucket(table, key):
        return table.setdefault(key, {"n": 0, "keys": {}})

    def add(b, keys):
        b["n"] += 1
        for k in keys:
            k = _safe(str(k), _KEY_RE) or "<invalid key>"
            b["keys"][k] = b["keys"].get(k, 0) + 1

    for rec in recs:
        ev, tn, sid = _top(rec)
        add(bucket(by_event, _safe(ev, _EVENT_RE) or ("<none>" if ev is None else "<invalid>")),
            rec.get("keys") or rec["raw"])
        add(bucket(by_tool, _safe(tn) or ("<none>" if tn is None else "<invalid>")),
            rec.get("tool_input_keys") or [])
        if sid is not None:
            sessions.add(sid)

    def table(title, t):
        lines = [f"{title}", f"  {'name':<24} {'count':>5}  keys (present/count)"]
        for k, b in sorted(t.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
            ks = ", ".join(f"{kk} {n}/{b['n']}" for kk, n in sorted(b["keys"].items()))
            lines.append(f"  {k:<24} {b['n']:>5}  {ks}")
        return lines

    out = [f"payloads: {len(recs)}   non-object lines: {other}   sessions: {len(sessions)}", ""]
    out += table("per event: top-level keys", by_event) + [""]
    out += table("per tool: tool_input keys", by_tool)
    return "\n".join(out) + "\n"
