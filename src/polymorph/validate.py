"""Mapping validation (schema 1). Pure data checks; never executes or loads anything."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

EXTRACTORS = ["apply_patch_paths"]  # shipped registry; names only, code lives elsewhere
CLASSES = ("shell", "edit", "read", "mcp", "other")
EVENTS = ("session_start", "pre_tool", "post_tool")
CAPS = ("ask", "post_event", "exit_2_blocks", "rewrites_input", "json_answers")
OUT_KINDS = ("allow", "deny", "ask", "warn", "remind")
OUT_FIELDS = {"exit": int, "stdout": str, "stderr": str}

REQUIRED = ("schema", "name", "config_path", "format", "template",
            "tools", "events", "capabilities", "payload_in", "payload_out")
OPTIONAL = ("verified", "context_file", "skills_dir")

_PATH = re.compile(r"^[A-Za-z_][\w-]*(\.[\w-]+)*$")
_TOOL = re.compile(r"^[A-Za-z_][\w.*-]*$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_PLACEHOLDER = re.compile(r"\{[a-z_][a-z0-9_.]*\}")
_CODEY = re.compile(r"\$\(|\$\{|`|;|\|\||&&|\||>|<|\\|\n|\.\./")


def _codey(s: str) -> bool:
    return bool(_CODEY.search(_PLACEHOLDER.sub("", s)))


def _unknown(errs: list[str], where: str, d: dict, allowed) -> None:
    for k in d:
        if k not in allowed:
            errs.append(f"{where}: unknown key {k!r}")


def validate_mapping(src: Any) -> list[str]:
    """Return a list of error strings; empty means valid. src: path or dict."""
    if isinstance(src, (str, Path)):
        try:
            with open(src, "rb") as f:
                m = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            return [f"cannot read mapping: {e}"]
    else:
        m = src
    errs: list[str] = []
    if not isinstance(m, dict):
        return ["mapping must be a table"]
    for k in REQUIRED:
        if k not in m:
            errs.append(f"missing required key {k!r}")
    _unknown(errs, "top level", m, REQUIRED + OPTIONAL)
    if "schema" in m and (m["schema"] != 1 or isinstance(m["schema"], bool)):
        errs.append(f"schema must be 1, got {m['schema']!r}")
    for k in ("name", "verified", "config_path", "format", "template", "context_file", "skills_dir"):
        if k in m and not isinstance(m[k], str):
            errs.append(f"{k} must be a string")
    if isinstance(m.get("name"), str) and not _NAME.match(m["name"]):
        errs.append(f"name {m['name']!r} must match [a-z][a-z0-9_-]*")
    for k in ("config_path", "context_file", "skills_dir"):
        v = m.get(k)
        if isinstance(v, str) and (v.startswith("/") or ".." in v.split("/") or _codey(v)):
            errs.append(f"{k} must be a plain relative path, got {v!r}")
    if isinstance(m.get("template"), str) and not _NAME.match(m["template"]):
        errs.append(f"template {m['template']!r} must be a plain name")
    if isinstance(m.get("format"), str) and m["format"] not in ("json", "toml", "yaml"):
        errs.append(f"format {m['format']!r} not one of json, toml, yaml")

    t = m.get("tools")
    if t is not None:
        if not isinstance(t, dict):
            errs.append("tools must be a table")
        else:
            _unknown(errs, "tools", t, CLASSES)
            for cls, v in t.items():
                names = [v] if isinstance(v, str) else v
                if not isinstance(names, list) or not names:
                    errs.append(f"tools.{cls} must be a string or non-empty list of strings")
                    continue
                for n in names:
                    if not isinstance(n, str) or not _TOOL.match(n):
                        errs.append(f"tools.{cls}: bad tool name {n!r}")

    e = m.get("events")
    if e is not None:
        if not isinstance(e, dict):
            errs.append("events must be a table")
        else:
            _unknown(errs, "events", e, EVENTS)
            for k, v in e.items():
                if not isinstance(v, str) or not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", v):
                    errs.append(f"events.{k}: bad native event name {v!r}")

    c = m.get("capabilities")
    if c is not None:
        if not isinstance(c, dict):
            errs.append("capabilities must be a table")
        else:
            _unknown(errs, "capabilities", c, CAPS)
            for k in CAPS:
                if k not in c:
                    errs.append(f"capabilities.{k} is required")
                elif not isinstance(c[k], bool):
                    errs.append(f"capabilities.{k} must be a boolean")

    pi = m.get("payload_in")
    if pi is not None:
        if not isinstance(pi, dict):
            errs.append("payload_in must be a table")
        else:
            for k, v in pi.items():
                if k == "extractor":
                    if v not in EXTRACTORS:
                        errs.append(f"payload_in.extractor {v!r} not in registry {EXTRACTORS}")
                    continue
                base = k.split(".", 1)
                if len(base) == 2 and base[0] not in CLASSES:
                    errs.append(f"payload_in key {k!r}: unknown class {base[0]!r}")
                elif len(base) == 1 and k not in ("tool_name", "cwd", "session_id", "tool_response", "event"):
                    errs.append(f"payload_in: unknown canonical key {k!r}")
                if not isinstance(v, str) or not _PATH.match(v):
                    errs.append(f"payload_in.{k}: must be a dotted path, got {v!r}")

    po = m.get("payload_out")
    if po is not None:
        if not isinstance(po, dict):
            errs.append("payload_out must be a table")
        else:
            _unknown(errs, "payload_out", po, OUT_KINDS)
            for kind, spec in po.items():
                if not isinstance(spec, dict):
                    errs.append(f"payload_out.{kind} must be a table")
                    continue
                _unknown(errs, f"payload_out.{kind}", spec, OUT_FIELDS)
                for f, v in spec.items():
                    want = OUT_FIELDS.get(f)
                    if want is None:
                        continue
                    if not isinstance(v, want) or (want is int and isinstance(v, bool)):
                        errs.append(f"payload_out.{kind}.{f} must be {want.__name__}")
                    elif isinstance(v, str) and _codey(v):
                        errs.append(f"payload_out.{kind}.{f}: code-like value {v!r}")
    return errs
