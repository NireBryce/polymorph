"""Generate per-harness hook configs from a Canon."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .canon import Canon, Hook
from .mapping import Mapping, load_mapping
from .reader_claude import read_claude

ENABLED_FILE = ".agents/polymorph/config.toml"


@dataclass
class TargetResult:
    harness: str
    path: str
    content: str
    drift: bool = False
    unmapped: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
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


def _claude_shape(canon: Canon, m: Mapping, existing: dict, wrapped: bool) -> tuple[str, list[str]]:
    """wrapped: ZCode shape (hooks.enabled + hooks.events, per-hook enabled); else Claude Code settings shape."""
    gaps: list[str] = []
    events: dict[str, list[dict]] = {}
    for h in canon.hooks:
        native = m.events.get(h.event)
        if native is None:
            gaps.append(f"{h.script}: no equivalent, {m.name} has no {h.event} event")
            continue
        matcher, missing = _matcher(h, m)
        if missing:
            gaps.append(f"{h.script}: no {m.name} tool for class(es) {', '.join(missing)}")
            if matcher is not None and not matcher:
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
    return json.dumps(doc, indent=2) + "\n", gaps


def _claude_like(canon, m, existing):
    return _claude_shape(canon, m, existing, wrapped=True)


def _claude_settings(canon, m, existing):
    return _claude_shape(canon, m, existing, wrapped=False)


TEMPLATES = {"claude-like": _claude_like, "claude-settings": _claude_settings}


def generate_target(repo: Path, canon: Canon, m: Mapping) -> TargetResult:
    if m.format != "json" or m.template not in TEMPLATES:
        raise ValueError(f"{m.name}: unsupported format/template {m.format}/{m.template}")
    out = repo / m.config_path
    existing: dict = {}
    old = None
    if out.is_file():
        old = out.read_text()
        existing = json.loads(old)
    content, gaps = TEMPLATES[m.template](canon, m, existing)
    warnings = []
    if not m.verified:
        warnings.append(f"{m.name}: mapping has no `verified` field; treat output as unverified")
    return TargetResult(
        harness=m.name, path=m.config_path, content=content, drift=(old != content),
        unmapped=list(canon.unmapped), gaps=gaps, warnings=warnings,
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
