"""Generate per-harness hook configs from a Canon."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .canon import Canon, Hook
from .decisions import Applied, Decision, DecisionError, Mismatch, load_decisions, options_for
from .mapping import Mapping, load_mapping
from .reader_claude import read_claude

ENABLED_FILE = ".polymorph/basecamp/config.toml"
DECISIONS_FILE_FMT = ".polymorph/basecamp/decisions/{}.toml"


@dataclass
class TargetResult:
    harness: str
    path: str
    content: str
    drift: bool = False
    unmapped: list[str] = field(default_factory=list)
    undecided: list[Mismatch] = field(default_factory=list)
    applied: list[Applied] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    native_events: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def enabled_targets(repo: Path) -> list[str]:
    config_file = repo / ENABLED_FILE
    if not config_file.is_file():
        return []
    data = tomllib.loads(config_file.read_text())
    bad = set(data) - {"enabled"}
    if bad:
        raise ValueError(f"{config_file}: unknown key(s) {sorted(bad)}")
    return list(data.get("enabled", []))


def adapter_command(harness: str, script: str, timeout: int | None = None) -> str:
    extra = f" --timeout {timeout}" if timeout is not None else ""
    # python3, never python: on a python3-only system a bare `python` fails with
    # exit 127 before polymorph runs, and a nonzero non-2 exit does not block
    return (
        f'python3 -m polymorph adapter --harness {harness}{extra} -- '
        f'"$CLAUDE_PROJECT_DIR/{script}"'
    )


def direct_command(script: str) -> str:
    """The hook runs bare: the harness already speaks the canonical protocol."""
    return f'"$CLAUDE_PROJECT_DIR/{script}"'


def hook_command(mapping: Mapping, script: str, timeout: int | None = None) -> str:
    if mapping.delivery == "direct":
        return direct_command(script)
    return adapter_command(mapping.name, script, timeout)


def _matcher(hook: Hook, mapping: Mapping) -> tuple[str | None, list[str]]:
    """Native matcher for a hook; second value lists classes with no native tool."""
    if not hook.classes:
        return None, []
    names: list[str] = []
    missing: list[str] = []
    for tool_class in hook.classes:
        if tool_class in mapping.tools:
            names.extend(tool_name for tool_name in mapping.tools[tool_class] if tool_name not in names)
        else:
            missing.append(tool_class)
    return "|".join(names), missing


# Canonical payload fields a hook of each tool class reads (any one of a tuple satisfies it).
# polymorph does not read hook scripts, so this is a model by class, not a scan.
CLASS_FIELDS = {
    "shell": ("shell.command",),
    "edit": ("edit.file_path",),
    "read": ("read.file_path", "read.path", "read.pattern", "read.glob"),
}
BASE_FIELDS = ("tool_name", "cwd", "session_id", "tool_response", "event")


def _provided(mapping: Mapping, tool_class: str) -> bool:
    keys = set(mapping.payload_in)
    if tool_class == "edit" and "extractor" in keys:
        return True
    return bool(keys & set(CLASS_FIELDS[tool_class]))


def hook_mismatches(hook: Hook, mapping: Mapping) -> list[Mismatch]:
    """Everything the target lacks that this hook needs, before any decision.

    The id carries the hook's canonical event: one script registered under several events
    gets one mismatch per event, and one decision answers only its own (issue #37).
    """
    out: list[Mismatch] = []
    base = dict(script=hook.script, event=hook.event, classes=hook.classes)
    if mapping.events.get(hook.event) is None:
        out.append(Mismatch(f"{hook.script}:{hook.event}:no-event", "no-event",
                            detail=f"{mapping.name} has no {hook.event} event",
                            options=options_for("no-event"), **base))
    _, missing = _matcher(hook, mapping)
    if missing:
        out.append(Mismatch(f"{hook.script}:{hook.event}:no-tool-class", "no-tool-class",
                            detail=f"{mapping.name} has no tool for class(es) {', '.join(missing)}",
                            options=options_for("no-tool-class"), **base))
    if mapping.delivery != "direct":  # direct delivery hands the hook the Claude payload unchanged
        lacking = [tool_class for tool_class in hook.classes if tool_class in CLASS_FIELDS and tool_class in mapping.tools and not _provided(mapping, tool_class)]
        if lacking:
            fields = ", ".join(CLASS_FIELDS[tool_class][0] if len(CLASS_FIELDS[tool_class]) == 1 else "read.*" for tool_class in lacking)
            out.append(Mismatch(f"{hook.script}:{hook.event}:missing-field", "missing-field",
                                detail=f"{mapping.name} payload_in does not map {fields}, which a {'/'.join(lacking)} hook reads",
                                options=options_for("missing-field"), **base))
    return out


@dataclass
class Resolution:
    undecided: list[Mismatch] = field(default_factory=list)
    applied: list[Applied] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)


def _apply(resolution: Resolution, mismatch: Mismatch, decision: Decision, weakening: bool, text: str) -> None:
    if not any(applied.id == mismatch.id for applied in resolution.applied):
        resolution.applied.append(Applied(mismatch.id, decision.choice, weakening, text))


def _extra_fields_note(canon: Canon, mapping: Mapping) -> list[str]:
    if mapping.delivery == "direct":
        return []
    tool_hooks = [hook for hook in canon.hooks if hook.event != "session_start"]
    classes = {tool_class for hook in tool_hooks for tool_class in (hook.classes or CLASS_FIELDS)}
    used = set(BASE_FIELDS) | {field for tool_class in classes for field in CLASS_FIELDS.get(tool_class, ())}
    extra = sorted(key for key in mapping.payload_in if key != "extractor" and key not in used)
    if not extra:
        return []
    return [f"{mapping.name}: payload_in maps {', '.join(extra)}, which no hook here reads (not a question)"]


def _claude_shape(canon: Canon, mapping: Mapping, existing: dict, wrapped: bool,
                  decisions: dict[str, Decision]) -> tuple[str, Resolution]:
    """wrapped: ZCode shape (hooks.enabled + hooks.events, per-hook enabled); else Claude Code settings shape."""
    resolution = Resolution()
    resolution.notes += _extra_fields_note(canon, mapping)
    events: dict[str, list[dict]] = {}
    for hook in canon.hooks:
        native = mapping.events.get(hook.event)
        matcher, _ = _matcher(hook, mapping)
        drop = False
        for mismatch in hook_mismatches(hook, mapping):
            resolution.seen.add(mismatch.id)
            decision = decisions.get(mismatch.id)
            if decision is None:
                if not any(existing.id == mismatch.id for existing in resolution.undecided):
                    resolution.undecided.append(mismatch)
                drop = True  # the default is skip; nothing is written while undecided
            elif mismatch.kind == "no-event" and decision.choice.startswith("substitute:"):
                native = decision.choice.split(":", 1)[1]
                canonical_event = next((e for e, n in mapping.events.items() if n == native), None)
                weak = canonical_event != hook.event
                _apply(resolution, mismatch, decision, weak, f"{hook.script} runs on {mapping.name} {native} instead of {hook.event}"
                       + (f" ({canonical_event} is a different event class)" if weak and canonical_event else ""))
            elif mismatch.kind == "missing-field" and decision.choice == "run-empty":
                _apply(resolution, mismatch, decision, True, f"{hook.script} runs without the field {mapping.name} lacks; it sees it empty")
            elif mismatch.kind == "no-tool-class":
                _apply(resolution, mismatch, decision, False, f"{hook.script} not registered for the class(es) {mapping.name} lacks")
                if matcher is not None and not matcher:
                    drop = True
            else:  # skip: no-event or missing-field
                _apply(resolution, mismatch, decision, False, f"{hook.script} not generated for {mapping.name}")
                drop = True
        if drop or native is None:
            continue
        entry: dict = {"type": "command", "command": hook_command(mapping, hook.script, hook.timeout)}
        if wrapped:
            entry["enabled"] = True
        if hook.timeout is not None:
            entry["timeout"] = hook.timeout
        groups = events.setdefault(native, [])
        for group in groups:
            if group.get("matcher") == matcher:
                group["hooks"].append(entry)
                break
        else:
            group = {"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]}
            groups.append(group)
    doc = dict(existing)
    doc["hooks"] = {"enabled": True, "events": events} if wrapped else events
    return json.dumps(doc, indent=2) + "\n", resolution


def _claude_like(canon, mapping, existing, decisions):
    return _claude_shape(canon, mapping, existing, True, decisions)


def _claude_settings(canon, mapping, existing, decisions):
    return _claude_shape(canon, mapping, existing, False, decisions)


TEMPLATES = {"claude-like": _claude_like, "claude-settings": _claude_settings}


def generate_target(repo: Path, canon: Canon, mapping: Mapping,
                    decisions: dict[str, Decision] | None = None) -> TargetResult:
    """decisions None: load the target's decision file; an invalid one raises DecisionError."""
    if mapping.format != "json" or mapping.template not in TEMPLATES:
        raise ValueError(f"{mapping.name}: unsupported format/template {mapping.format}/{mapping.template}")
    native_events = sorted(set(mapping.events.values()))
    if decisions is None:
        decisions, errs = load_decisions(repo, mapping.name, set(native_events))
        if errs:
            raise DecisionError(f"{DECISIONS_FILE_FMT.format(mapping.name)}: " + "; ".join(errs))
    out = repo / mapping.config_path
    # config_path comes from mapping data: refuse a target that leaves the repo
    # through a symlinked component, for the read here and the write in generate()
    real = Path(os.path.realpath(out))
    if not real.is_relative_to(Path(os.path.realpath(repo))):
        raise ValueError(f"{mapping.config_path!r} resolves outside the repository ({real}); "
                         "refusing to read or write it")
    existing: dict = {}
    previous_content = None
    if out.is_file():
        previous_content = out.read_text()
        existing = json.loads(previous_content)
        if not isinstance(existing, dict):
            raise ValueError(f"{out}: existing config is not a JSON object; refusing to overwrite it")
    content, resolution = TEMPLATES[mapping.template](canon, mapping, existing, decisions)
    warnings = []
    if not mapping.verified:
        warnings.append(f"{mapping.name}: mapping has no `verified` field; treat output as unverified")
    return TargetResult(
        harness=mapping.name, path=mapping.config_path, content=content,
        drift=(previous_content != content) and not resolution.undecided,
        unmapped=list(canon.unmapped), undecided=resolution.undecided, applied=resolution.applied,
        stale=sorted(set(decisions) - resolution.seen), notes=resolution.notes, native_events=native_events,
        warnings=warnings,
    )


def generate(
    repo: str | Path = ".",
    harnesses: list[str] | None = None,
    check: bool = False,
    mappings_dir=None,
    trust_dirs=(),
) -> list[TargetResult]:
    """Compute (and, unless check, write) config for each target."""
    repo = Path(repo).resolve()
    canon = read_claude(repo)
    names = harnesses or enabled_targets(repo)
    results = []
    for name in names:
        mapping = load_mapping(name, repo, mappings_dir, tuple(trust_dirs))
        target_result = generate_target(repo, canon, mapping)
        if not check and target_result.drift:
            dest = repo / target_result.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(target_result.content)
        results.append(target_result)
    return results
