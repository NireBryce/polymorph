"""Generate per-harness hook configs from a Canon."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .canon import Canon, Hook
from .decisions import Applied, Decision, DecisionError, Mismatch, load_decisions, options_for
from .mapping import Mapping, load_mapping
from .reader_claude import read_claude

ENABLED_FILE = ".agents/polymorph/config.toml"
DECISIONS_FILE_FMT = ".agents/polymorph/decisions/{}.toml"


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
    f = repo / ENABLED_FILE
    if not f.is_file():
        return []
    data = tomllib.loads(f.read_text())
    bad = set(data) - {"enabled"}
    if bad:
        raise ValueError(f"{f}: unknown key(s) {sorted(bad)}")
    return list(data.get("enabled", []))


def adapter_command(harness: str, script: str) -> str:
    return (
        f'python -m polymorph adapter --harness {harness} -- '
        f'"$CLAUDE_PROJECT_DIR/{script}"'
    )


def direct_command(script: str) -> str:
    """The hook runs bare: the harness already speaks the canonical protocol."""
    return f'"$CLAUDE_PROJECT_DIR/{script}"'


def hook_command(m: Mapping, script: str) -> str:
    if m.delivery == "direct":
        return direct_command(script)
    return adapter_command(m.name, script)


def _matcher(h: Hook, m: Mapping) -> tuple[str | None, list[str]]:
    """Native matcher for a hook; second value lists classes with no native tool."""
    if not h.classes:
        return None, []
    names: list[str] = []
    missing: list[str] = []
    for c in h.classes:
        if c in m.tools:
            names.extend(n for n in m.tools[c] if n not in names)
        else:
            missing.append(c)
    return "|".join(names), missing


# Canonical payload fields a hook of each tool class reads (any one of a tuple satisfies it).
# polymorph does not read hook scripts, so this is a model by class, not a scan.
CLASS_FIELDS = {
    "shell": ("shell.command",),
    "edit": ("edit.file_path",),
    "read": ("read.file_path", "read.path", "read.pattern", "read.glob"),
}
BASE_FIELDS = ("tool_name", "cwd", "session_id", "tool_response", "event")


def _provided(m: Mapping, cls: str) -> bool:
    keys = set(m.payload_in)
    if cls == "edit" and "extractor" in keys:
        return True
    return bool(keys & set(CLASS_FIELDS[cls]))


def hook_mismatches(h: Hook, m: Mapping) -> list[Mismatch]:
    """Everything the target lacks that this hook needs, before any decision."""
    out: list[Mismatch] = []
    base = dict(script=h.script, event=h.event, classes=h.classes)
    if m.events.get(h.event) is None:
        out.append(Mismatch(f"{h.script}:no-event", "no-event", detail=f"{m.name} has no {h.event} event",
                            options=options_for("no-event"), **base))
    _, missing = _matcher(h, m)
    if missing:
        out.append(Mismatch(f"{h.script}:no-tool-class", "no-tool-class",
                            detail=f"{m.name} has no tool for class(es) {', '.join(missing)}",
                            options=options_for("no-tool-class"), **base))
    if m.delivery != "direct":  # direct delivery hands the hook the Claude payload unchanged
        lacking = [c for c in h.classes if c in CLASS_FIELDS and c in m.tools and not _provided(m, c)]
        if lacking:
            fields = ", ".join(CLASS_FIELDS[c][0] if len(CLASS_FIELDS[c]) == 1 else "read.*" for c in lacking)
            out.append(Mismatch(f"{h.script}:missing-field", "missing-field",
                                detail=f"{m.name} payload_in does not map {fields}, which a {'/'.join(lacking)} hook reads",
                                options=options_for("missing-field"), **base))
    return out


@dataclass
class Resolution:
    undecided: list[Mismatch] = field(default_factory=list)
    applied: list[Applied] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)


def _apply(res: Resolution, mm: Mismatch, d: Decision, weakening: bool, text: str) -> None:
    if not any(a.id == mm.id for a in res.applied):
        res.applied.append(Applied(mm.id, d.choice, weakening, text))


def _extra_fields_note(canon: Canon, m: Mapping) -> list[str]:
    if m.delivery == "direct":
        return []
    tool_hooks = [h for h in canon.hooks if h.event != "session_start"]
    classes = {c for h in tool_hooks for c in (h.classes or CLASS_FIELDS)}
    used = set(BASE_FIELDS) | {f for c in classes for f in CLASS_FIELDS.get(c, ())}
    extra = sorted(k for k in m.payload_in if k != "extractor" and k not in used)
    if not extra:
        return []
    return [f"{m.name}: payload_in maps {', '.join(extra)}, which no hook here reads (not a question)"]


def _claude_shape(canon: Canon, m: Mapping, existing: dict, wrapped: bool,
                  decisions: dict[str, Decision]) -> tuple[str, Resolution]:
    """wrapped: ZCode shape (hooks.enabled + hooks.events, per-hook enabled); else Claude Code settings shape."""
    res = Resolution()
    res.notes += _extra_fields_note(canon, m)
    events: dict[str, list[dict]] = {}
    for h in canon.hooks:
        native = m.events.get(h.event)
        matcher, _ = _matcher(h, m)
        drop = False
        for mm in hook_mismatches(h, m):
            res.seen.add(mm.id)
            d = decisions.get(mm.id)
            if d is None:
                if not any(u.id == mm.id for u in res.undecided):
                    res.undecided.append(mm)
                drop = True  # the default is skip; nothing is written while undecided
            elif mm.kind == "no-event" and d.choice.startswith("substitute:"):
                native = d.choice.split(":", 1)[1]
                canon_ev = next((e for e, n in m.events.items() if n == native), None)
                weak = canon_ev != h.event
                _apply(res, mm, d, weak, f"{h.script} runs on {m.name} {native} instead of {h.event}"
                       + (f" ({canon_ev} is a different event class)" if weak and canon_ev else ""))
            elif mm.kind == "missing-field" and d.choice == "run-empty":
                _apply(res, mm, d, True, f"{h.script} runs without the field {m.name} lacks; it sees it empty")
            elif mm.kind == "no-tool-class":
                _apply(res, mm, d, False, f"{h.script} not registered for the class(es) {m.name} lacks")
                if matcher is not None and not matcher:
                    drop = True
            else:  # skip: no-event or missing-field
                _apply(res, mm, d, False, f"{h.script} not generated for {m.name}")
                drop = True
        if drop or native is None:
            continue
        entry: dict = {"type": "command", "command": hook_command(m, h.script)}
        if wrapped:
            entry["enabled"] = True
        if h.timeout is not None:
            entry["timeout"] = h.timeout
        groups = events.setdefault(native, [])
        for g in groups:
            if g.get("matcher") == matcher:
                g["hooks"].append(entry)
                break
        else:
            g = {"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]}
            groups.append(g)
    doc = dict(existing)
    doc["hooks"] = {"enabled": True, "events": events} if wrapped else events
    return json.dumps(doc, indent=2) + "\n", res


def _claude_like(canon, m, existing, decisions):
    return _claude_shape(canon, m, existing, True, decisions)


def _claude_settings(canon, m, existing, decisions):
    return _claude_shape(canon, m, existing, False, decisions)


TEMPLATES = {"claude-like": _claude_like, "claude-settings": _claude_settings}


def generate_target(repo: Path, canon: Canon, m: Mapping,
                    decisions: dict[str, Decision] | None = None) -> TargetResult:
    """decisions None: load the target's decision file; an invalid one raises DecisionError."""
    if m.format != "json" or m.template not in TEMPLATES:
        raise ValueError(f"{m.name}: unsupported format/template {m.format}/{m.template}")
    native_events = sorted(set(m.events.values()))
    if decisions is None:
        decisions, errs = load_decisions(repo, m.name, set(native_events))
        if errs:
            raise DecisionError(f"{DECISIONS_FILE_FMT.format(m.name)}: " + "; ".join(errs))
    out = repo / m.config_path
    existing: dict = {}
    old = None
    if out.is_file():
        old = out.read_text()
        existing = json.loads(old)
    content, res = TEMPLATES[m.template](canon, m, existing, decisions)
    warnings = []
    if not m.verified:
        warnings.append(f"{m.name}: mapping has no `verified` field; treat output as unverified")
    return TargetResult(
        harness=m.name, path=m.config_path, content=content,
        drift=(old != content) and not res.undecided,
        unmapped=list(canon.unmapped), undecided=res.undecided, applied=res.applied,
        stale=sorted(set(decisions) - res.seen), notes=res.notes, native_events=native_events,
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
        m = load_mapping(name, repo, mappings_dir, tuple(trust_dirs))
        r = generate_target(repo, canon, m)
        if not check and r.drift:
            dest = repo / r.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(r.content)
        results.append(r)
    return results
