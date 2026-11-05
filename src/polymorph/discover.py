"""Discovery mode: log what an unknown harness sends to hooks, then draft a mapping.

The log is untrusted data. Nothing read from it is executed, evaluated or shelled out;
the draft only copies back values that match strict name patterns (tool names, key
names), never command text or paths.

Tool-name heuristics (case-insensitive, first match wins, checked in this order):
  shell: bash, shell, terminal, command, exec, sh
  edit:  edit, write, patch, replace, create
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

DISCOVERY_DIR = ".polymorph/discovery"   # default log directory, under the current directory
RAW_IN_REPO_FLAG = "--i-really-want-this-in-the-repo-raw"
RAW_IN_REPO_ALIAS = "--i-really-want-this-in-the-repo"   # same flag, shorter spelling
_RULE = "=" * 72


def require_out_for_full(full: bool, out_given: bool, flag: str) -> None:
    """--full logs raw commands, file contents and paths. With no location the user chose it would
    land in the current directory, which is normally a repo: refuse, loudly, whatever else is passed."""
    if full and not out_given:
        raise ValueError("\n".join([
            _RULE,
            "REFUSED: --full without " + flag,
            _RULE,
            "--full records raw commands, file contents and paths, possibly secrets.",
            f"With no {flag} the log would go to a default location under the current directory",
            f"({DISCOVERY_DIR}/ or ./polymorph-capture-NAME), which is normally inside a git repo",
            "where `git add -A` commits it and a push publishes it.",
            f"Pass {flag} DIR to say where the raw log goes, and {RAW_IN_REPO_FLAG}",
            "to accept a plain-text log; you will then be asked to type yes.",
            _RULE]))


def containing_repo(path: str | Path) -> Path | None:
    """The git worktree root that contains `path` (which need not exist yet), or None."""
    p = Path(path).resolve()
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def confirm_full(path: str | Path, flag_given: bool, flag: str, stdin=None, stdout=None) -> None:
    """The gate in front of every --full: the long flag, then a typed `yes`. Raises ValueError for no.

    The flag is required whatever the location, and a typed answer is required after it even
    when the flag was given: the flag alone is a habit, the word is a decision. No terminal means
    no answer, so it is refused. Nothing here encrypts or hides the log."""
    if not flag_given:
        raise ValueError("\n".join([
            _RULE,
            "REFUSED: --full needs " + RAW_IN_REPO_FLAG,
            _RULE,
            "--full records raw commands, file contents and paths, possibly secrets, in plain text.",
            "Nothing hides or encrypts the log, and secret scanners will not be told it is there.",
            f"To accept that, add {RAW_IN_REPO_FLAG} (or {RAW_IN_REPO_ALIAS}); you will then be",
            "asked to type yes.",
            _RULE]))
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    repo = containing_repo(path)
    where = f"inside the git repo {repo}" if repo else "outside any git repo"
    banner = "\n".join([
        _RULE,
        "WARNING: A RAW LOG, IN PLAIN TEXT",
        _RULE,
        f"{flag} {path} is {where}.",
        "A --full log holds raw commands, file contents and paths, possibly secrets.",
        "It is not encrypted or hidden. `git add -A` would stage it; a push would publish it.",
        _RULE])
    if not getattr(stdin, "isatty", lambda: False)():
        raise ValueError(f"{banner}\nNot a terminal, so there is nobody to type yes: refused.")
    stdout.write(banner + '\nType "yes" to write the raw log: ')
    stdout.flush()
    if stdin.readline().strip().lower() != "yes":
        raise ValueError("cancelled: nothing was written.")


_TOOL_RE = re.compile(r"^[A-Za-z_][\w.*-]*$")
_KEY_RE = re.compile(r"^[A-Za-z_][\w-]*$")
_EVENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")   # names are only ever printed

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
# Unknown names ending in these are a guess (OpenCode: execute.before, execute.after).
_EVENT_SUFFIX = (("pre_tool", "before"), ("post_tool", "after"))


def _event_kind(ev: str) -> tuple[str | None, bool]:
    """(canonical event, guessed) for a name already known to match _EVENT_RE."""
    low = ev.lower()
    for canon, words in _EVENT_CANON:
        if low in words:
            return canon, False
    for canon, suffix in _EVENT_SUFFIX:
        if low.endswith(suffix):
            return canon, True
    return None, False


# ---- init -------------------------------------------------------------------

def log_command(harness: str, log_dir: str | Path | None = None, redact: bool = True,
                out: str | Path | None = None, env: bool = False,
                pythonpath: str | Path | None = None) -> str:
    """Hook command: absolute interpreter and log path, so it works from any cwd.

    pythonpath: prefix the command with `env PYTHONPATH=...`, for a polymorph that is run from a
    source tree rather than installed (`capture` sets it; `discover init` does not)."""
    if out is None:
        out = Path(log_dir or Path.cwd() / DISCOVERY_DIR).resolve() / f"{harness}.jsonl"
    flag = ("" if redact else " --full") + (" --env-be-very-careful" if env else "")
    pythonpath_prefix = f"env PYTHONPATH={shlex.quote(str(pythonpath))} " if pythonpath else ""
    return (f"{pythonpath_prefix}{shlex.quote(sys.executable)} -m polymorph discover log{flag} "
            f"--out {shlex.quote(str(Path(out).resolve()))}")


def _check_harness(harness: str) -> None:
    if not re.match(r"^[a-z][a-z0-9_-]*$", harness):
        raise ValueError(f"harness name {harness!r} must match [a-z][a-z0-9_-]*")


def _resolve(config_path: str, base: str | Path) -> Path:
    path = Path(config_path)
    return path if path.is_absolute() else Path(base) / path


def _load_doc(config_file: Path) -> dict:
    doc: dict = {}
    if config_file.is_file():
        try:
            doc = json.loads(config_file.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(f"{config_file}: existing config is not valid JSON ({e}); refusing to touch the file") from e
        if not isinstance(doc, dict):
            raise ValueError(f"{config_file}: existing config is not a JSON object")
    return doc


def _hooks_tables(doc: dict, shape: str, event_name: str) -> tuple[dict, dict, list]:
    """The existing (hooks table, event table, group list) for event_name, validated.

    Raises ValueError, naming the offender, if a part that has to be kept cannot be
    parsed: nothing is written then, so the user's hooks are never guessed at.
    """
    hooks = doc.get("hooks")
    if hooks is None:
        hooks = {}
    if not isinstance(hooks, dict):
        raise ValueError("existing 'hooks' is not an object; refusing to touch the file")
    table = hooks
    if shape == "claude-like":
        table = hooks.get("events")
        if table is None:
            table = {}
        if not isinstance(table, dict):
            raise ValueError("existing 'hooks.events' is not an object; refusing to touch the file")
    groups = table.get(event_name)
    if groups is None:
        groups = []
    if not isinstance(groups, list) or not all(
            isinstance(group, dict) and isinstance(group.get("hooks", []), list) for group in groups):
        raise ValueError(f"existing hooks for {event_name!r} are not a list of matcher groups; "
                         "refusing to touch the file")
    return hooks, table, groups


def _merge_hook(doc: dict, shape: str, event_name: str, matcher: str, entry: dict,
                marker: str | None = None) -> int:
    """The one merge: add a catch-all hook entry as its own matcher group under event_name.

    Every other group, event and key stays. With a marker, entries whose command
    contains it are removed first (a repeat call replaces its own entry instead of
    duplicating it); returns how many were removed.
    """
    hooks, table, groups = _hooks_tables(doc, shape, event_name)
    removed = 0
    if marker:
        marker_doc = {"hooks": {event_name: groups}}
        removed = _strip_marker(marker_doc, marker)
        groups = marker_doc["hooks"][event_name]
    group = {"matcher": matcher, "hooks": [entry]}
    if shape == "claude-like":
        entry["enabled"] = True
        doc["hooks"] = {**hooks, "enabled": True, "events": {**table, event_name: [*groups, group]}}
    else:
        doc["hooks"] = {**hooks, event_name: [*groups, group]}
    return removed


def _write_doc(config_file: Path, doc: dict) -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_file.with_name(config_file.name + ".polymorph-tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n")
    os.replace(tmp, config_file)


def init_config(harness: str, config_path: str, event_name: str = "PreToolUse",
                matcher: str = "*", base: str | Path = ".", shape: str | None = None,
                log_dir: str | Path | None = None, redact: bool = True, env: bool = False,
                pythonpath: str | Path | None = None) -> Path:
    """shape: claude-settings (hooks.<Event>, Claude Code and its SDK) or claude-like (ZCode);
    None detects from the existing file, falling back to claude-settings."""
    if shape is not None and shape not in ("claude-settings", "claude-like"):
        raise ValueError(f"unknown shape {shape!r}")
    _check_harness(harness)
    config_file = _resolve(config_path, base)
    doc = _load_doc(config_file)
    if shape is None:
        shape = _detect_shape(doc)   # installing where the harness never fires logs nothing
    entry: dict = {"type": "command", "command": log_command(harness, log_dir, redact, env=env, pythonpath=pythonpath)}
    # the logger's own entry is recognised by its --out file; the user's hooks stay
    _merge_hook(doc, shape, event_name, matcher, entry,
                marker=("-m polymorph discover log", f"/{harness}.jsonl"))
    _write_doc(config_file, doc)
    return config_file


# ---- reload-check -----------------------------------------------------------

def _state_path(scratch: Path) -> Path:
    return scratch.with_name(scratch.name + ".reload-state.json")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _detect_shape(doc: dict) -> str:
    hooks = doc.get("hooks")
    return "claude-like" if isinstance(hooks, dict) and isinstance(hooks.get("events"), dict) else "claude-settings"


def reload_check_start(config_path: str, scratch_log: str, event_name: str = "PreToolUse",
                       matcher: str = "*", base: str | Path = ".", shape: str | None = None) -> str:
    """Add a temporary extra hook (distinct out file); return instructions for the user."""
    config_file = _resolve(config_path, base).resolve()
    scratch_log_path = Path(scratch_log).resolve()
    state_path = _state_path(scratch_log_path)
    if state_path.exists():
        raise ValueError(f"{state_path} exists: a reload check is already open; run it with --finish first")
    original = config_file.read_bytes() if config_file.is_file() else None
    doc = _load_doc(config_file)
    entry = {"type": "command", "command": log_command("reload-check", out=scratch_log_path)}
    _merge_hook(doc, shape or _detect_shape(doc), event_name, matcher, entry)
    # state first: if the config write fails midway, finish can still clean up
    scratch_log_path.parent.mkdir(parents=True, exist_ok=True)
    state = {"config": str(config_file), "existed": original is not None,
             "original_b64": base64.b64encode(original or b"").decode()}
    state_path.write_text(json.dumps(state))
    try:
        _write_doc(config_file, doc)
    except Exception:
        _restore(config_file, state)
        state_path.unlink(missing_ok=True)
        raise
    state["written_sha256"] = _sha(config_file.read_bytes())
    state_path.write_text(json.dumps(state))
    return "\n".join([
        f"Temporary hook added to {config_file} ({event_name}), logging to {scratch_log_path}.",
        "Do NOT restart the harness or reload its config. In the already-running session:",
        "  1. have the agent make exactly one tool call (e.g. run `true` with the shell tool),",
        "  2. then run:",
        f"     python -m polymorph discover reload-check --finish --config-path {shlex.quote(str(config_file))}"
        f" --scratch-log {shlex.quote(str(scratch_log_path))}",
        "The finish step reports whether the running harness picked up the edit and removes the hook.",
    ])


def _restore(config_file: Path, state: dict) -> None:
    if state["existed"]:
        config_file.write_bytes(base64.b64decode(state["original_b64"]))
    else:
        config_file.unlink(missing_ok=True)


def _strip_marker(doc: dict, marker: str | tuple[str, ...]) -> int:
    """Remove hook entries whose command carries every marker (one, or a tuple of all)."""
    markers = (marker,) if isinstance(marker, str) else tuple(marker)
    removed_count = 0

    def strip_groups(group_list):
        nonlocal removed_count
        out = []
        for group in group_list:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                keep = [hook for hook in group["hooks"]
                        if not (isinstance(hook, dict) and all(marker_part in str(hook.get("command", "")) for marker_part in markers))]
                removed_count += len(group["hooks"]) - len(keep)
                if not keep:
                    continue
                group = {**group, "hooks": keep}
            out.append(group)
        return out

    hooks_table = doc.get("hooks")
    if isinstance(hooks_table, dict):
        tables = [hooks_table] + ([hooks_table["events"]] if isinstance(hooks_table.get("events"), dict) else [])
        for table in tables:
            for k, v in list(table.items()):
                if isinstance(v, list):
                    table[k] = strip_groups(v)
    return removed_count


def reload_check_finish(config_path: str, scratch_log: str, base: str | Path = ".") -> tuple[bool, str]:
    """Remove the temporary hook and report whether the scratch log got a line."""
    config_file = _resolve(config_path, base).resolve()
    scratch_log_path = Path(scratch_log).resolve()
    state_path = _state_path(scratch_log_path)
    seen = scratch_log_path.is_file() and any(line.strip() for line in scratch_log_path.read_text(errors="replace").splitlines())
    notes = []
    if state_path.is_file():
        state = json.loads(state_path.read_text())
        current_bytes = config_file.read_bytes() if config_file.is_file() else None
        if current_bytes is not None and _sha(current_bytes) == state.get("written_sha256"):
            _restore(config_file, state)
            notes.append(f"{config_file} restored byte-for-byte")
        elif current_bytes is None:
            notes.append(f"{config_file} is gone; nothing to clean up")
        else:
            doc = _load_doc(config_file)
            removed = _strip_marker(doc, str(scratch_log_path))
            if removed:
                _write_doc(config_file, doc)
            notes.append(f"{config_file} changed since the check began; removed {removed} temporary hook(s) "
                         "by marker instead of restoring")
        state_path.unlink(missing_ok=True)
    else:
        doc = _load_doc(config_file)
        removed = _strip_marker(doc, str(scratch_log_path))
        if removed:
            _write_doc(config_file, doc)
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


LOG_SCHEMA = 1   # lines written before this marker existed have no "schema" key and still read
_SESSION_KEYS = ("session_id", "sessionId")
_RESPONSE_KEYS = ("tool_response", "toolResponse")
_JSON_TYPES = ((bool, "boolean"), (int, "number"), (float, "number"), (str, "string"),
               (list, "array"), (dict, "object"), (type(None), "null"))

# Plugin provenance (--plugin-version, --location), recorded verbatim alongside schema so a
# line says which plugin generation wrote it and where its session runs (an event bus can be
# server-global: one log holds lines from several working directories). A value is kept only
# when short and free of shell characters; an odd one becomes INVALID, an absent one leaves
# the key off, which reads as unmarked in the summary.
_PROVENANCE_VALUE = re.compile(r"^[A-Za-z0-9_.:/@+-]{1,128}$")


def _provenance_mark(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    # fullmatch, not match: `$` alone also matches before a trailing newline
    return value if _PROVENANCE_VALUE.fullmatch(value) else INVALID


# Values kept verbatim under redaction: structure, never user content. Everything
# else (cwd, transcript_path, prompts, tool_input, tool_response, ...) is reduced
# to {type, length}.
_SAFE_VALUES = ("hook_event_name", "hookEventName", "event", "tool_name", "toolName",
                "session_id", "sessionId",
                "permission_mode", "effort", "source", "duration_ms")


def _descriptor(v) -> dict:
    typ = next(n for t, n in _JSON_TYPES if isinstance(v, t))
    data = v if isinstance(v, str) else json.dumps(v, sort_keys=True, ensure_ascii=False)
    return {"type": typ, "length": len(data)}


# Call and message ids, kept verbatim only when they look like ids, so before/after lines
# can be paired. Anything else under these names is reduced like any other value.
_CALL_ID_KEYS = ("tool_use_id", "toolCallId", "id", "call_id", "callID", "toolCallID", "prompt_id")
_MESSAGE_ID_KEYS = ("messageID", "message_id")
_ID_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
# The id shape class also matches common credential formats (they are word/dot/dash
# soup too); a value under an id-named key that looks like one is reduced instead.
_CRED_VALUE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]|github_pat_|sk-[A-Za-z0-9]|xox[baprs]-|glpat-"
    r"|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\.)")


def _id_shaped(value: str) -> bool:
    # fullmatch: a `$` anchor alone also matches before a trailing newline
    return _CRED_VALUE.search(value) is None and bool(_ID_VALUE.fullmatch(value))

# Output-like objects keep their nested key paths (depth-limited), values become descriptors.
_OUTPUT_KEYS = (*_RESPONSE_KEYS, "output", "result", "tool_result", "toolResult")
_SHAPE_DEPTH = 3
_SHAPE_MAX_KEYS = 64


def _shape(v, depth: int = _SHAPE_DEPTH):
    """Nested key paths of an object with every leaf a descriptor; odd key names become <invalid key>."""
    if not isinstance(v, dict) or depth <= 0:
        return _descriptor(v)
    out = {}
    for k, child in list(v.items())[:_SHAPE_MAX_KEYS]:
        k = str(k)
        out[k if _safe(k, _KEY_RE) else "<invalid key>"] = _shape(child, depth - 1)
    return out


INVALID = "<invalid>"   # stands in for a name or id that failed its pattern in a redacted log


def _safe_text(key: str, value: str) -> bool:
    """A string under a _SAFE_VALUES key survives redaction only when it looks like a name or id."""
    if key in ("session_id", "sessionId"):
        return _id_shaped(value)
    if key in ("hook_event_name", "hookEventName", "event"):
        return _safe(value, _EVENT_RE) is not None
    if key in ("tool_name", "toolName"):
        return _safe(value) is not None
    return _id_shaped(value)   # permission_mode, effort, source, duration_ms


def _redact_payload(raw):
    """Keys kept. Surviving verbatim: _SAFE_VALUES names that look like names, id-shaped values
    under the id keys, and output-like objects shaped to depth 3; everything else becomes {type, length}."""
    if not isinstance(raw, dict):
        return _descriptor(raw)
    out = {}
    for k, v in raw.items():
        if k in _SAFE_VALUES and isinstance(v, (int, float, bool)):
            out[k] = v
        elif k in _SAFE_VALUES and isinstance(v, str) and _safe_text(k, v):
            out[k] = v
        elif (k in (*_CALL_ID_KEYS, *_MESSAGE_ID_KEYS) and isinstance(v, str)
              and _id_shaped(v)):
            out[k] = v
        elif k in _OUTPUT_KEYS and isinstance(v, dict):
            out[k] = _shape(v)
        elif k in _INPUT_KEYS and isinstance(v, dict):
            out[k] = {(kk if _safe(str(kk), _KEY_RE) else "<invalid key>"): _descriptor(vv)
                      for kk, vv in v.items()}
        else:
            out[k] = _descriptor(v)
    return out


# Only with --env-be-very-careful: names (never values) of variables that say where the project is.
_ENV_NAME = re.compile(r"PROJECT|WORKSPACE|HARNESS", re.I)


def _env_keys() -> list[str]:
    return sorted(k for k in os.environ if _ENV_NAME.search(k))


def _record(text: str, redact: bool = True, env: bool = False,
            plugin_version: str | None = None, location: str | None = None) -> dict:
    try:
        raw = json.loads(text)
    except (ValueError, RecursionError):
        raw = text
    keys: list = []
    tool_input_keys: list = []
    event = tool_name = session_id = None
    if isinstance(raw, dict):
        keys = sorted(_safe(str(k), _KEY_RE) or "<invalid key>" for k in raw)
        for k in _INPUT_KEYS:
            if isinstance(raw.get(k), dict):
                tool_input_keys = sorted(_safe(str(input_key), _KEY_RE) or "<invalid key>" for input_key in raw[k])
                break
        event = _first_str(raw, _EVENT_KEYS)
        tool_name = _first_str(raw, _TOOL_NAME_KEYS)
        session_id = _first_str(raw, _SESSION_KEYS)
        if redact:   # a name is kept only when it looks like a name: the key aliases are not all safe
            # an odd value is replaced by a marker, so "a payload with a bad name" is still countable
            event = None if event is None else (_safe(event, _EVENT_RE) or INVALID)
            tool_name = None if tool_name is None else (_safe(tool_name) or INVALID)
            session_id = None if session_id is None else (session_id if _id_shaped(session_id) else INVALID)
    rec: dict = {
        "schema": LOG_SCHEMA,
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
    version_mark, location_mark = _provenance_mark(plugin_version), _provenance_mark(location)
    if version_mark:
        rec["plugin_version"] = version_mark
    if location_mark:
        rec["location"] = location_mark
    return rec


def _log_error(out: str, what: str) -> None:
    """Sidecar <name>.errors.jsonl beside the log; best effort, never raises."""
    try:
        log_path = Path(out)
        side = log_path.with_name(log_path.stem + ".errors.jsonl")
        side.parent.mkdir(parents=True, exist_ok=True)
        with open(side, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "error": what}) + "\n")
    except Exception:
        pass


def log_stdin(out: str, stream=None, redact: bool = True, env: bool = False,
              plugin_version: str | None = None, location: str | None = None) -> None:
    """Append one record. Never raises, never writes stdout: must not affect the harness."""
    try:
        stream = stream or sys.stdin
        text = stream.read()
        rec = _record(text, redact, env, plugin_version, location)
        try:
            stdin_is_object = isinstance(json.loads(text), dict)
            why = "stdin JSON is not an object"
        except (ValueError, RecursionError):
            stdin_is_object, why = False, "stdin is not JSON"
        if not stdin_is_object:
            _log_error(out, f"{why} ({len(text)} chars)")
        line = json.dumps(rec, ensure_ascii=False)
        log_path = Path(out)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
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
    name_lower = name.lower()
    if name_lower.startswith("mcp"):
        return None
    for tool_class, words in _HEURISTICS:
        if any(word == name_lower or name_lower.startswith(word) or name_lower.endswith(word) for word in words):
            return tool_class
    return None


def _load_records(path: str | Path) -> tuple[list[dict], int, int]:
    """(log lines whose raw payload is an object, non-object lines, unparseable lines)."""
    recs, other, broken = [], 0, 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            broken += 1
            continue
        if not isinstance(rec, dict):
            other += 1
            continue
        for field_name in ("keys", "tool_input_keys"):
            field_value = rec.get(field_name)
            if field_value is not None and not (isinstance(field_value, list) and all(isinstance(key_name, str) for key_name in field_value)):
                del rec[field_name]   # untrusted shape: treated as absent rather than crashing summary
        if isinstance(rec.get("raw"), dict):
            recs.append(rec)
        else:
            other += 1
    return recs, other, broken


def _top(rec: dict) -> tuple[str | None, str | None, str | None]:
    """event, tool_name, session_id from the line's top level (older logs: derived once)."""
    if "event" in rec or "tool_name" in rec:
        return rec.get("event"), rec.get("tool_name"), rec.get("session_id")
    raw = rec["raw"]
    return (_first_str(raw, _EVENT_KEYS), _first_str(raw, _TOOL_NAME_KEYS),
            _first_str(raw, _SESSION_KEYS))


def _safe(s, pat=_TOOL_RE) -> str | None:
    # fullmatch, not match: `$` alone also matches before a trailing newline
    return s if isinstance(s, str) and len(s) <= 64 and pat.fullmatch(s) else None


# read-class tool_input keys worth mapping when seen: canonical field -> tool_input key names
_READ_FIELDS = (("read.file_path", ("file_path", "filePath")), ("read.path", ("path",)),
                ("read.pattern", ("pattern",)), ("read.glob", ("glob",)))


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _select(recs: list[dict], include=(), events=()) -> tuple[list[tuple[int, dict]], dict[str, int]]:
    """(file position, record) pairs in ts order, plus counts of the hidden event names.

    Default: tool events (a tool name is present). `events` replaces that default with
    exactly those event names; `include` adds names to whatever is selected. Each hook
    event is logged by its own process, so file order is not time order: sort by ts
    (file order breaks ties).
    """
    keep, hidden = [], {}
    for i, rec in enumerate(recs):
        event_name, tool_name, _ = _top(rec)
        if events:
            wanted = event_name in events
        else:
            wanted = tool_name is not None
        if wanted or event_name in include:
            keep.append((i, rec))
        else:
            name = _safe(event_name, _EVENT_RE) or ("<none>" if event_name is None else "<invalid>")
            hidden[name] = hidden.get(name, 0) + 1
    keep.sort(key=lambda entry: (_num(entry[1].get("ts")), entry[0]))
    return keep, hidden


def _ids(raw: dict) -> tuple[str | None, str | None]:
    def pick(names):
        for key_name in names:
            value = raw.get(key_name)
            if isinstance(value, str) and _ID_VALUE.fullmatch(value):
                return value
        return None
    return pick(_CALL_ID_KEYS), pick(_MESSAGE_ID_KEYS)


def _pairing(sel: list[tuple[int, dict]]) -> dict[str, int] | None:
    """Pair pre_tool and post_tool lines by (call id, message id); None if there are none."""
    positions_by_pair: dict[tuple, dict[str, int]] = {}
    no_id = 0
    any_pre_or_post = False
    for i, rec in sel:
        event_name = _top(rec)[0]
        kind = _event_kind(event_name)[0] if event_name and _EVENT_RE.match(event_name) else None
        if kind not in ("pre_tool", "post_tool"):
            continue
        any_pre_or_post = True
        call_id, message_id = _ids(rec["raw"])
        if call_id is None:
            no_id += 1
            continue
        positions_by_pair.setdefault((call_id, message_id), {}).setdefault(kind, i)
    if not any_pre_or_post:
        return None
    paired = [positions for positions in positions_by_pair.values() if len(positions) == 2]
    return {
        "paired": len(paired),
        "out_of_order": sum(1 for positions in paired if positions["post_tool"] < positions["pre_tool"]),
        "before_only": sum(1 for positions in positions_by_pair.values() if set(positions) == {"pre_tool"}),
        "after_only": sum(1 for positions in positions_by_pair.values() if set(positions) == {"post_tool"}),
        "no_id": no_id,
    }


def _pairing_text(p: dict[str, int]) -> str:
    return (f"{p['paired']} paired ({p['out_of_order']} arrived out of order), "
            f"{p['before_only']} before without after, {p['after_only']} after without before, "
            f"{p['no_id']} without an id")


def _slowest(selected: list[tuple[int, dict]], keep: int = 5) -> list[tuple[float, str, str]] | None:
    """The slowest paired pre->post deltas as (duration_seconds, tool name, call id); None without one.

    Same pairing key as _pairing; a delta needs both lines and a post at or after its pre
    (an earlier post is clock and file-order noise, already counted out of order there).
    The delta is the gap between the two logger lines' write times -- one process per hook
    event writes at its start -- so it approximates what the harness waited on the pre-hook:
    a hook awaited out to its timeout kill shows as a delta near that timeout, which is the
    fail-open latency no payload ever shows.
    """
    write_times_by_pair: dict[tuple, dict[str, tuple[float, str]]] = {}
    for _, record in selected:
        event_name = _top(record)[0]
        kind = _event_kind(event_name)[0] if event_name and _EVENT_RE.match(event_name) else None
        if kind not in ("pre_tool", "post_tool"):
            continue
        call_id, message_id = _ids(record["raw"])
        if call_id is None:
            continue
        write_time = _num(record.get("ts"))
        tool_name = _safe(_top(record)[1]) or "<no name>"
        pair_times = write_times_by_pair.setdefault((call_id, message_id), {})
        pair_times.setdefault(kind, (write_time, tool_name))
    deltas = []
    for (call_id, _message_id), times in write_times_by_pair.items():
        if len(times) == 2 and times["post_tool"][0] >= times["pre_tool"][0]:
            duration_seconds = times["post_tool"][0] - times["pre_tool"][0]
            deltas.append((duration_seconds, times["pre_tool"][1], call_id))
    deltas.sort(key=lambda item: (-item[0], item[2]))
    return deltas[:keep] or None


def _is_descriptor(v) -> bool:
    return (isinstance(v, dict) and set(v) == {"type", "length"} and isinstance(v["type"], str)
            and isinstance(v["length"], int))


def _leaf_paths(v, prefix: str, depth: int = _SHAPE_DEPTH + 1):
    """(dotted path, type) of every leaf of a redacted object; unsafe key names end the path."""
    if _is_descriptor(v):
        yield prefix, v["type"]
    elif isinstance(v, dict) and depth > 0:
        for k, child in v.items():
            if _safe(k, _KEY_RE):
                yield from _leaf_paths(child, f"{prefix}.{k}", depth - 1)
    elif isinstance(v, dict):
        yield prefix, "object"
    else:
        yield prefix, next(n for t, n in _JSON_TYPES if isinstance(v, t))


def _is_bool(v) -> bool:
    return isinstance(v, bool) or (_is_descriptor(v) and v["type"] == "boolean")


def draft_mapping(log: str | Path, name: str, include=(), events=()) -> str:
    if not re.match(r"^[a-z][a-z0-9_-]*$", name):
        raise ValueError(f"name {name!r} must match [a-z][a-z0-9_-]*")
    all_records, _, _ = _load_records(log)
    selected, _ = _select(all_records, include, events)
    records = [rec for _, rec in selected]
    total = len(records)
    tools: dict[str, list[str]] = {}
    tool_calls: dict[str, int] = {}        # safe tool name -> payloads
    skipped: dict[str, str] = {}           # safe tool name -> reason
    unnamed_bad = 0
    event_names: dict[str, str] = {}
    event_counts: dict[str, int] = {}
    event_guessed: dict[str, bool] = {}
    seen_events: dict[str, int] = {}       # every safe event name -> payloads
    payload_in_paths: dict[str, str] = {}
    # canonical key -> {tool or "*": [present, seen]}
    evidence: dict[str, dict[str, int]] = {}
    presence_counts: dict[str, int] = {}
    claude_style = False
    flags: dict[str, dict[str, int]] = {}  # tool -> boolean tool_input key -> payloads
    out_paths: dict[str, dict[str, int]] = {}  # output-like path -> {type: payloads}
    out_total = 0
    for rec in all_records:
        census_event = _top(rec)[0]
        if census_event is not None and len(census_event) <= 64 and _EVENT_RE.match(census_event):
            seen_events[census_event] = seen_events.get(census_event, 0) + 1

    def set_payload(key: str, path: str):
        payload_in_paths.setdefault(key, path)

    def count_evidence(key: str, tool_name: str):
        evidence.setdefault(key, {})
        evidence[key][tool_name] = evidence[key].get(tool_name, 0) + 1

    for rec in records:
        raw = rec["raw"]
        event_name, tool_name, session_id = _top(rec)
        tool = _safe(tool_name)
        if tool_name is not None and tool is None:
            unnamed_bad += 1
        tool_key = _first_key(raw, _TOOL_NAME_KEYS)
        if tool_key and _KEY_RE.match(tool_key) and tool_name is not None:
            set_payload("tool_name", tool_key)
            presence_counts["tool_name"] = presence_counts.get("tool_name", 0) + 1
        tool_class = None
        if tool:
            tool_calls[tool] = tool_calls.get(tool, 0) + 1
            tool_class = classify_tool(tool)
            if tool_class:
                if tool not in tools.setdefault(tool_class, []):
                    tools[tool_class].append(tool)
            else:
                skipped[tool] = ("mcp tool: not classified, no name heuristic applies"
                                 if tool.lower().startswith("mcp")
                                 else "no name heuristic matched")
        input_key = _first_key(raw, _INPUT_KEYS)
        if tool_class and input_key and isinstance(raw[input_key], dict) and _KEY_RE.match(input_key):
            tool_input = raw[input_key]
            for kk, vv in tool_input.items():
                if _safe(str(kk), _KEY_RE) and _is_bool(vv):
                    flags.setdefault(tool, {})
                    flags[tool][kk] = flags[tool].get(kk, 0) + 1
            if tool_class == "shell":
                command_key = _first_key(tool_input, _COMMAND_KEYS)
                if command_key and _KEY_RE.match(command_key):
                    set_payload("shell.command", f"{input_key}.{command_key}")
                    count_evidence("shell.command", tool)
            elif tool_class == "edit":
                path_key = _first_key(tool_input, _PATH_KEYS)
                if path_key and _KEY_RE.match(path_key):
                    set_payload("edit.file_path", f"{input_key}.{path_key}")
                    count_evidence("edit.file_path", tool)
            else:
                for canon, names in _READ_FIELDS:
                    read_input_key = _first_key(tool_input, names)
                    if read_input_key and _KEY_RE.match(read_input_key):
                        set_payload(canon, f"{input_key}.{read_input_key}")
                        count_evidence(canon, tool)
        output_key = _first_key(raw, _OUTPUT_KEYS)
        if output_key and _KEY_RE.match(output_key) and isinstance(raw[output_key], dict):
            out_total += 1
            for path, type_name in _leaf_paths(raw[output_key], output_key):
                type_counts = out_paths.setdefault(path, {})
                type_counts[type_name] = type_counts.get(type_name, 0) + 1
        if "cwd" in raw:
            set_payload("cwd", "cwd")
            presence_counts["cwd"] = presence_counts.get("cwd", 0) + 1
        if session_id is not None:
            set_payload("session_id", "session_id" if "session_id" in raw else
                        _first_key(raw, _SESSION_KEYS) or "session_id")
            presence_counts["session_id"] = presence_counts.get("session_id", 0) + 1
        if event_name is not None and len(event_name) <= 64 and _EVENT_RE.match(event_name):
            canon, guessed = _event_kind(event_name)
            if canon and event_names.setdefault(canon, event_name) == event_name:
                event_guessed[canon] = guessed
                event_counts[canon] = event_counts.get(canon, 0) + 1
        if "hook_event_name" in raw and "cwd" in raw:
            claude_style = True

    def evidence_text(key: str) -> str:
        if key in presence_counts:
            return f"{presence_counts[key]}/{total} payloads"
        parts = []
        for tool_name, count in sorted(evidence.get(key, {}).items()):
            parts.append(f"{count}/{tool_calls.get(tool_name, count)} {tool_name}")
        return ", ".join(parts)

    quote = lambda s: json.dumps(s)
    out = [
        f"# DRAFT mapping for {name}, drafted from {total} observed payloads. UNVERIFIED.",
        "# Produced by `polymorph discover draft`. Tool classes come from name heuristics;",
        "# anything not observed is left out rather than guessed. No command text or paths",
        "# from the log are included. Review before use; `verified` is deliberately absent.",
        "# Counts in comments are present/observed over the payloads of that tool.",
        "schema = 1",
        f"name = {quote(name)}",
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
    for tool_class in ("shell", "edit", "read"):
        if tool_class in tools:
            call_count = sum(tool_calls.get(tool_name, 0) for tool_name in tools[tool_class])
            out.append(f"{tool_class} = [{', '.join(quote(tool_name) for tool_name in tools[tool_class])}]   # guessed from name ({call_count} calls)")
    if skipped or unnamed_bad:
        out.append("# Not classified (left out, not guessed):")
        for tool_name, reason in sorted(skipped.items()):
            out.append(f"#   {tool_name} ({tool_calls[tool_name]} calls): {reason}")
        if unnamed_bad:
            out.append(f"#   {unnamed_bad} payload(s) with a tool name that fails the name pattern: ignored")
    out += ["", "[events]"]
    for canon, _ in _EVENT_CANON:
        if canon in event_names:
            count_text = f"{event_counts[canon]}/{total} payloads"
            out.append(f"{canon} = {quote(event_names[canon])}   # " +
                       (f"guessed from name ({count_text})" if event_guessed[canon] else count_text))
    rest = {event_name: payload_count for event_name, payload_count in seen_events.items()
            if event_name not in event_names.values()}
    if rest:
        out.append("# Other event names observed (not mapped):")
        for event_name, payload_count in sorted(rest.items(), key=lambda item: (-item[1], item[0])):
            out.append(f"#   {event_name} ({payload_count} payloads)")
    out += [
        "",
        "[capabilities]",
        "# Not observable from payloads; all false (conservative) until checked live.",
        "ask = false", "post_event = false", "exit_2_blocks = false",
        "rewrites_input = false", "json_answers = false",
        "",
        "[payload_in]",
    ]
    for key, path in payload_in_paths.items():
        key_evidence = evidence_text(key)
        out.append(f"{quote(key) if '.' in key else key} = {quote(path)}" + (f"   # {key_evidence}" if key_evidence else ""))
    if flags:
        out.append("# Observed tool_input flags (boolean keys; present/observed over the tool's payloads):")
        for tool_name in sorted(flags):
            flags_text = ", ".join(f"{flag_key} {flag_count}/{tool_calls.get(tool_name, flag_count)}"
                                   for flag_key, flag_count in sorted(flags[tool_name].items()))
            out.append(f"#   {tool_name}: {flags_text}")
    out += ["", "[payload_out]"]
    if out_paths:
        out.append(f"# Output-like keys observed (values hidden by redaction; {out_total} payloads):")
        for path in sorted(out_paths):
            types = ", ".join(f"{type_name} {count}" for type_name, count in sorted(out_paths[path].items()))
            out.append(f"#   {path}: {types}")
    out.append("")
    return "\n".join(out)


# ---- summary ----------------------------------------------------------------

def summarize(log: str | Path, include=(), events=()) -> str:
    all_records, non_object_count, broken = _load_records(log)
    selected, hidden = _select(all_records, include, events)
    records = [rec for _, rec in selected]
    by_event: dict[str, dict] = {}
    by_tool: dict[str, dict] = {}
    sessions: set = set()

    def bucket(table, key):
        return table.setdefault(key, {"n": 0, "keys": {}})

    def add(bucket_entry, keys):
        bucket_entry["n"] += 1
        for k in keys:
            k = _safe(str(k), _KEY_RE) or "<invalid key>"
            bucket_entry["keys"][k] = bucket_entry["keys"].get(k, 0) + 1

    for rec in records:
        event_name, tool_name, session_id = _top(rec)
        add(bucket(by_event, _safe(event_name, _EVENT_RE) or ("<none>" if event_name is None else "<invalid>")),
            rec.get("keys") or rec["raw"])
        add(bucket(by_tool, _safe(tool_name) or ("<none>" if tool_name is None else "<invalid>")),
            rec.get("tool_input_keys") or [])
        if session_id is not None:
            sessions.add(session_id)

    def table(title, buckets):
        lines = [f"{title}", f"  {'name':<24} {'count':>5}  keys (present/count)"]
        for name, bucket in sorted(buckets.items(), key=lambda item: (-item[1]["n"], item[0])):
            keys_text = ", ".join(f"{kk} {count}/{bucket['n']}" for kk, count in sorted(bucket["keys"].items()))
            lines.append(f"  {name:<24} {bucket['n']:>5}  {keys_text}")
        return lines

    def marks(key: str) -> dict[str, int]:
        """Value counts for a provenance key; anything missing or odd-shaped reads as unmarked."""
        counts: dict[str, int] = {}
        for record in all_records:
            value = record.get(key)
            ok = (isinstance(value, int) and not isinstance(value, bool)) if key == "schema" \
                else (isinstance(value, str) and bool(value))
            mark = str(value) if ok else "unmarked"
            counts[mark] = counts.get(mark, 0) + 1
        return counts

    def mark_line(label: str, key: str) -> str:
        return label + " " + ", ".join(f"{mark} ({count})" for mark, count in sorted(marks(key).items()))

    out = [f"payloads: {len(records)}   non-object lines: {non_object_count}   sessions: {len(sessions)}"]
    if broken:
        out.append(f"unparseable lines: {broken} (truncated or corrupt; not counted as payloads)")
    out.append(mark_line("log line schema:", "schema"))
    out.append(mark_line("plugin versions:", "plugin_version"))
    out.append(mark_line("locations:", "location"))
    if hidden:
        out.append("hidden: " + ", ".join(f"{event_name} {count}" for event_name, count in
                                          sorted(hidden.items(), key=lambda item: (-item[1], item[0])))
                   + "   (--include EVENT adds, --event EVENT selects)")
    pairing = _pairing(selected)
    if pairing:
        out.append("pairing by call id and message id: " + _pairing_text(pairing))
    deltas = _slowest(selected)
    if deltas:
        out.append("slowest pre->post deltas (logger write time; includes the hook wait):")
        for duration_seconds, tool_name, call_id in deltas:
            out.append(f"  {duration_seconds:.3f}s  {tool_name}  {call_id}")
    per_session: dict[str, list[tuple[int, dict]]] = {}
    for i, rec in selected:
        session_key = _top(rec)[2]
        # old-format lines (only `raw`) skip the write-time id check, so check here
        session_key = "<no session id>" if session_key is None else (session_key if _id_shaped(session_key) else INVALID)
        per_session.setdefault(session_key, []).append((i, rec))
    if len(per_session) > 1:
        out.append("per session: payloads, events, pairing")
        for session_key, group in sorted(per_session.items(), key=lambda item: (-len(item[1]), item[0])):
            counts: dict[str, int] = {}
            for _, rec in group:
                name = _safe(_top(rec)[0], _EVENT_RE) \
                    or ("<none>" if _top(rec)[0] is None else "<invalid>")
                counts[name] = counts.get(name, 0) + 1
            events_text = ", ".join(f"{event_name} {count}" for event_name, count in
                                    sorted(counts.items(), key=lambda item: (-item[1], item[0])))
            line = f"  {session_key}  {len(group)} payloads  {events_text}"
            pairing = _pairing(group)
            if pairing:
                line += ";  " + _pairing_text(pairing)
            out.append(line)
    out.append("")
    out += table("per event: top-level keys", by_event) + [""]
    out += table("per tool: tool_input keys", by_tool)
    return "\n".join(out) + "\n"
