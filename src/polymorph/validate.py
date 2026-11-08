"""Mapping validation (schema 1). Pure data checks; never executes or loads anything."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from .canonical import CANON_PATHS
from .exprs import placeholder_names

EXTRACTORS = ["apply_patch_paths"]  # shipped registry; names only, code lives elsewhere
CLASSES = ("shell", "edit", "read", "mcp", "other")
EVENTS = ("session_start", "pre_tool", "post_tool", "stop", "user_prompt_submit")
CAPS = ("ask", "post_event", "exit_2_blocks", "rewrites_input", "json_answers")
OUT_KINDS = ("allow", "deny", "ask", "warn", "remind")
OUT_FIELDS = {"exit": int, "stdout": str, "stderr": str}
KNOWN_PLACEHOLDERS = {"reason", "context", "message"}

REQUIRED = ("schema", "name", "config_path", "format", "template",
            "tools", "events", "capabilities", "payload_in", "payload_out")
OPTIONAL = ("verified", "context_file", "skills_dir", "delivery")
DELIVERIES = ("adapter", "direct")
# payload_in of a harness that speaks the Claude hook protocol unchanged; the only
# translation `delivery = "direct"` may skip.
IDENTITY_PAYLOAD_IN = CANON_PATHS

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


# A direct mapping's matchers are the harness's own tool names, and the harness
# speaks the canonical protocol: a renamed tool would silently never match, so
# the four classed kinds must use their canonical names (`other` is unconstrained).
CANONICAL_TOOLS = {"shell": {"Bash"}, "edit": {"Edit", "Write", "MultiEdit"},
                   "read": {"Read", "Grep", "Glob"}}


def direct_errors(mapping: dict) -> list[str]:
    """Why delivery = "direct" is not allowed for this mapping (empty = allowed)."""
    errs: list[str] = []
    if mapping.get("payload_out"):
        errs.append('delivery "direct" requires an empty payload_out (answers cannot be translated)')
    payload_in = mapping.get("payload_in")
    if isinstance(payload_in, dict):
        for k, v in payload_in.items():
            if k not in IDENTITY_PAYLOAD_IN:
                errs.append(f'delivery "direct" requires an identity payload_in; {k!r} is not canonical')
            elif v != IDENTITY_PAYLOAD_IN[k]:
                errs.append(f'delivery "direct" requires payload_in.{k} = '
                            f'{IDENTITY_PAYLOAD_IN[k]!r}, got {v!r}')
    tools = mapping.get("tools")
    if isinstance(tools, dict):
        for cls, v in tools.items():
            names = [v] if isinstance(v, str) else (v if isinstance(v, list) else [])
            for name in names:
                if not isinstance(name, str):
                    continue
                if cls == "mcp" and not name.startswith("mcp__"):
                    errs.append(f'delivery "direct" requires tools.mcp names to start with '
                                f'mcp__, got {name!r}')
                elif cls in CANONICAL_TOOLS and name not in CANONICAL_TOOLS[cls]:
                    errs.append(f'delivery "direct" requires tools.{cls} to be '
                                f'{", ".join(sorted(CANONICAL_TOOLS[cls]))}, got {name!r}')
    return errs


def validate_mapping(src: Any) -> list[str]:
    """Return a list of error strings; empty means valid. src: path or dict."""
    if isinstance(src, (str, Path)):
        try:
            with open(src, "rb") as handle:
                mapping = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as events:
            return [f"cannot read mapping: {events}"]
    else:
        mapping = src
    errs: list[str] = []
    if not isinstance(mapping, dict):
        return ["mapping must be a table"]
    for k in REQUIRED:
        if k not in mapping:
            errs.append(f"missing required key {k!r}")
    _unknown(errs, "top level", mapping, REQUIRED + OPTIONAL)
    if "schema" in mapping and (mapping["schema"] != 1 or isinstance(mapping["schema"], bool)):
        errs.append(f"schema must be 1, got {mapping['schema']!r}")
    for k in ("name", "verified", "config_path", "format", "template", "context_file", "skills_dir"):
        if k in mapping and not isinstance(mapping[k], str):
            errs.append(f"{k} must be a string")
    if isinstance(mapping.get("name"), str) and not _NAME.match(mapping["name"]):
        errs.append(f"name {mapping['name']!r} must match [a-z][a-z0-9_-]*")
    for k in ("config_path", "context_file", "skills_dir"):
        value = mapping.get(k)
        if isinstance(value, str) and (value.startswith("/") or ".." in value.split("/") or _codey(value)):
            errs.append(f"{k} must be a plain relative path, got {value!r}")
    if "delivery" in mapping:
        if mapping["delivery"] not in DELIVERIES:
            errs.append(f"delivery must be one of {', '.join(DELIVERIES)}, got {mapping['delivery']!r}")
        elif mapping["delivery"] == "direct":
            errs += direct_errors(mapping)
    if isinstance(mapping.get("template"), str) and not _NAME.match(mapping["template"]):
        errs.append(f"template {mapping['template']!r} must be a plain name")
    if isinstance(mapping.get("format"), str) and mapping["format"] not in ("json", "toml", "yaml"):
        errs.append(f"format {mapping['format']!r} not one of json, toml, yaml")

    tools = mapping.get("tools")
    if tools is not None:
        if not isinstance(tools, dict):
            errs.append("tools must be a table")
        else:
            _unknown(errs, "tools", tools, CLASSES)
            for cls, value in tools.items():
                names = [value] if isinstance(value, str) else value
                if not isinstance(names, list) or not names:
                    errs.append(f"tools.{cls} must be a string or non-empty list of strings")
                    continue
                for n in names:
                    if not isinstance(n, str) or not _TOOL.match(n):
                        errs.append(f"tools.{cls}: bad tool name {n!r}")

    events = mapping.get("events")
    if events is not None:
        if not isinstance(events, dict):
            errs.append("events must be a table")
        else:
            _unknown(errs, "events", events, EVENTS)
            for k, value in events.items():
                if not isinstance(value, str) or not re.match(r"^[A-Za-z][A-Za-z0-9_.:-]*$", value):
                    errs.append(f"events.{k}: bad native event name {value!r}")

    caps = mapping.get("capabilities")
    if caps is not None:
        if not isinstance(caps, dict):
            errs.append("capabilities must be a table")
        else:
            _unknown(errs, "capabilities", caps, CAPS)
            for k in CAPS:
                if k not in caps:
                    errs.append(f"capabilities.{k} is required")
                elif not isinstance(caps[k], bool):
                    errs.append(f"capabilities.{k} must be a boolean")

    payload_in = mapping.get("payload_in")
    if payload_in is not None:
        if not isinstance(payload_in, dict):
            errs.append("payload_in must be a table")
        else:
            for k, value in payload_in.items():
                if k == "extractor":
                    if value not in EXTRACTORS:
                        errs.append(f"payload_in.extractor {value!r} not in registry {EXTRACTORS}")
                    continue
                key_parts = k.split(".", 1)
                if len(key_parts) == 2:
                    # the adapter places a classed key via CANON_PATHS; an unknown one would
                    # land at a top-level dotted path nothing in the canonical payload reads
                    if key_parts[0] not in CLASSES:
                        errs.append(f"payload_in key {k!r}: unknown class {key_parts[0]!r}")
                    elif k not in CANON_PATHS:
                        known = ", ".join(sorted(p for p in CANON_PATHS if "." in p))
                        errs.append(f"payload_in key {k!r} is not a canonical field (known: {known})")
                elif k not in ("tool_name", "cwd", "session_id", "tool_response", "event"):
                    errs.append(f"payload_in: unknown canonical key {k!r}")
                if not isinstance(value, str) or not _PATH.match(value):
                    errs.append(f"payload_in.{k}: must be a dotted path, got {value!r}")

    payload_out = mapping.get("payload_out")
    if payload_out is not None:
        if not isinstance(payload_out, dict):
            errs.append("payload_out must be a table")
        else:
            _unknown(errs, "payload_out", payload_out, OUT_KINDS)
            for kind, spec in payload_out.items():
                if not isinstance(spec, dict):
                    errs.append(f"payload_out.{kind} must be a table")
                    continue
                if kind == "ask" and isinstance(caps, dict) and caps.get("ask") is False:
                    # SPEC "Wire protocol": the hook hardens ask to deny (it reads
                    # GUARD_ASK_ENFORCED=0); a blocking entry would decide that for every hook,
                    # and a non-blocking one fails the exit rule below, so neither belongs here
                    errs.append("payload_out.ask is set while capabilities.ask is false: "
                                "hardening ask is the hook's job (GUARD_ASK_ENFORCED), not the "
                                "mapping's; remove the entry")
                    continue
                _unknown(errs, f"payload_out.{kind}", spec, OUT_FIELDS)
                for field, value in spec.items():
                    want = OUT_FIELDS.get(field)
                    if want is None:
                        continue
                    if not isinstance(value, want) or (want is int and isinstance(value, bool)):
                        errs.append(f"payload_out.{kind}.{field} must be {want.__name__}")
                    elif field == "exit" and kind in ("deny", "ask") and value != 2:
                        # 2 is the only documented block code; the adapter defaults a
                        # missing exit to 0, and any other code does not block
                        errs.append(f"payload_out.{kind}.exit must be 2, the canonical block "
                                    f"exit, got {value!r} (nothing else blocks; 0 is an allow)")
                    elif isinstance(value, str):
                        bad = sorted(placeholder_names(value) - KNOWN_PLACEHOLDERS)
                        if bad:
                            errs.append(f"payload_out.{kind}.{field}: unknown placeholder(s) {bad} "
                                        f"(known: {', '.join(sorted(KNOWN_PLACEHOLDERS))})")
                        elif _codey(value):
                            errs.append(f"payload_out.{kind}.{field}: code-like value {value!r}")
                if kind in ("deny", "ask") and "exit" not in spec:
                    errs.append(f"payload_out.{kind}.exit is missing and must be 2 "
                                f"(the default exit 0 renders the {kind} as an allow)")
    errs += deny_route_errors(mapping)
    return errs


def deny_route_errors(mapping: dict) -> list[str]:
    """A deny must reach the harness as something that blocks. With no payload_out.deny the
    adapter falls back to JSON pass-through (json_answers) or exit 2 (exit_2_blocks); with
    neither it can only refuse the deny at run time (a NOT-checked fault, and the call
    proceeds), so such a mapping is refused here instead."""
    capabilities, payload_out = mapping.get("capabilities"), mapping.get("payload_out")
    if mapping.get("delivery") == "direct":
        return []   # hooks run bare and speak the canonical protocol; no adapter renders a deny
    if not isinstance(capabilities, dict) or not isinstance(payload_out, dict) or "deny" in payload_out:
        return []
    if capabilities.get("json_answers") is True or capabilities.get("exit_2_blocks") is True:
        return []
    return ["payload_out.deny is required when neither capabilities.json_answers nor "
            "capabilities.exit_2_blocks is true: without it a guard deny cannot block"]
