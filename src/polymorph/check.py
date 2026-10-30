"""`polymorph check`: static analysis of a repo's agent config.

Never executes a hook script, a harness or the network, and never writes files.
Each check is a small function returning Findings with a stable code.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .conformance import fixture_files, run_fixtures
from .generate import enabled_targets, generate_target
from .mapping import BUILTIN_DIR, Mapping, MappingError, load_mapping
from .reader_claude import SETTINGS, ReaderError, read_claude, resolve_script
from .validate import validate_mapping

SEVERITIES = ("error", "warning", "note")

# code -> (default severity, description); the SARIF rules come from here.
CODES = {
    "no-targets": ("error", "No harness is enabled and none was passed with --harness."),
    "settings-invalid": ("error", "The Claude settings file is missing or unreadable."),
    "mapping-invalid": ("error", "A target's mapping fails validation or cannot be loaded."),
    "mapping-unverified": ("warning", "A mapping has no `verified` field."),
    "mapping-untested-live": ("note", "Block semantics and ask enforcement are unproven without a live test."),
    "hook-outside-hooks-dir": ("error", "A hook command is not a bare script under .agents/hooks/."),
    "hook-missing": ("error", "A hook script does not exist."),
    "hook-not-executable": ("error", "A hook script is not executable."),
    "hook-no-equivalent": ("warning", "A hook's event or tool class has no native equivalent in the target."),
    "setting-unmapped": ("note", "A setting has no cross-harness meaning."),
    "generated-drift": ("error", "A target's generated file differs from what generate would write."),
    "fixtures-missing": ("warning", "A built-in mapping has no conformance fixtures."),
    "fixtures-skipped": ("note", "Fixtures are not checked for a repo-local mapping."),
    "fixture-failed": ("error", "A conformance fixture does not pass."),
}


@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    target: str | None
    message: str
    file: str | None = None


def _f(code: str, target: str | None, message: str, file: str | None = None) -> Finding:
    return Finding(CODES[code][0], code, target, message, file)


def _rel(repo: Path, p: str | Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(repo))
    except ValueError:
        return str(p)


# --- checks -----------------------------------------------------------------

def check_hook_scripts(repo: Path) -> list[Finding]:
    """Every hook command in the settings file: under hooks dir, exists, executable."""
    out: list[Finding] = []
    try:
        data = json.loads((repo / SETTINGS).read_text())
        if not isinstance(data, dict):
            raise ValueError("top level must be an object")
    except FileNotFoundError:
        return [_f("settings-invalid", None, f"{SETTINGS} not found", SETTINGS)]
    except ValueError as e:
        return [_f("settings-invalid", None, f"{SETTINGS}: {e}", SETTINGS)]
    for event, groups in (data.get("hooks") or {}).items():
        for group in groups or []:
            for h in group.get("hooks", []):
                if h.get("type", "command") != "command":
                    continue
                cmd = h.get("command", "")
                try:
                    rel = resolve_script(cmd, repo)
                except ReaderError as e:
                    out.append(_f("hook-outside-hooks-dir", None, f"{event}: {e}", SETTINGS))
                    continue
                path = repo / rel
                if not path.is_file():
                    out.append(_f("hook-missing", None, f"{event}: {rel} does not exist", rel))
                elif not os.access(path, os.X_OK):
                    out.append(_f("hook-not-executable", None, f"{event}: {rel} is not executable", rel))
    return out


def check_mapping(repo: Path, name: str, m: Mapping) -> list[Finding]:
    out = [_f("mapping-invalid", name, e, _rel(repo, m.source)) for e in validate_mapping(m.source)]
    if not m.verified:
        out.append(_f("mapping-unverified", name, "mapping has no `verified` field; treat output as unverified",
                      _rel(repo, m.source)))
    out.append(_f("mapping-untested-live", name, "not tested live: block semantics, ask enforcement unproven"))
    return out


def check_fixtures(repo: Path, name: str, m: Mapping) -> list[Finding]:
    """Built-in mappings only: replay fixtures/<name>/ through the adapter."""
    if Path(m.source).resolve().parent != BUILTIN_DIR.resolve():
        return [_f("fixtures-skipped", name, "repo-local mapping: fixtures not checked", _rel(repo, m.source))]
    if not fixture_files(name):
        return [_f("fixtures-missing", name, f"no fixtures under fixtures/{name}/")]
    _, failures = run_fixtures(name)
    return [_f("fixture-failed", name, msg) for msg in failures]


# --- driver -----------------------------------------------------------------

def run_check(repo: str | Path = ".", harnesses: list[str] | None = None) -> list[Finding]:
    repo = Path(repo).resolve()
    findings: list[Finding] = []
    try:
        names = list(harnesses or enabled_targets(repo))
    except (ValueError, OSError) as e:
        raise ValueError(f"cannot read enabled targets: {e}") from e
    if not names:
        return [_f("no-targets", None, "nothing enabled: pass --harness or list targets in "
                   ".agents/polymorph/config.toml")]

    findings += check_hook_scripts(repo)
    try:
        canon = read_claude(repo)
    except ReaderError:
        canon = None  # already reported by check_hook_scripts

    for name in names:
        try:
            m = load_mapping(name, repo, warn=lambda msg: None)
        except MappingError as e:
            findings.append(_f("mapping-invalid", name, str(e)))
            continue
        findings += check_mapping(repo, name, m)
        findings += check_fixtures(repo, name, m)
        if canon is None:
            continue
        try:
            r = generate_target(repo, canon, m)
        except (ValueError, OSError) as e:
            findings.append(_f("generated-drift", name, f"cannot compute generated file: {e}", m.config_path))
            continue
        findings += [_f("hook-no-equivalent", name, g) for g in r.gaps]
        findings += [_f("setting-unmapped", name, u) for u in r.unmapped]
        if r.drift:
            state = "missing" if not (repo / r.path).is_file() else "differs from generate output"
            findings.append(_f("generated-drift", name, f"{r.path} {state}; run `polymorph generate`", r.path))
    return findings


def summarize(findings: list[Finding]) -> dict[str, int]:
    return {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES}


def exit_code(findings: list[Finding], strict: bool = False) -> int:
    s = summarize(findings)
    return 1 if s["error"] or (strict and s["warning"]) else 0


# --- formats ----------------------------------------------------------------

def format_text(findings: list[Finding]) -> str:
    lines: list[str] = []
    groups: dict[str | None, list[Finding]] = {}
    for f in findings:
        groups.setdefault(f.target, []).append(f)
    for target in sorted(groups, key=lambda t: (t is not None, t or "")):
        lines.append(f"[{target}]" if target else "[repo]")
        for f in groups[target]:
            where = f" ({f.file})" if f.file else ""
            lines.append(f"  {f.severity} {f.code} {target or '-'}: {f.message}{where}")
    s = summarize(findings)
    lines.append(f"{s['error']} error(s), {s['warning']} warning(s), {s['note']} note(s)")
    return "\n".join(lines) + "\n"


def format_json(findings: list[Finding]) -> str:
    return json.dumps({"findings": [asdict(f) for f in findings], "summary": summarize(findings)}, indent=2) + "\n"


def format_sarif(findings: list[Finding]) -> str:
    used = sorted({f.code for f in findings})
    results = []
    for f in findings:
        res: dict = {
            "ruleId": f.code,
            "level": f.severity,
            "message": {"text": f"{f.target}: {f.message}" if f.target else f.message},
        }
        if f.file:
            res["locations"] = [{"physicalLocation": {"artifactLocation": {"uri": f.file}}}]
        results.append(res)
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "polymorph",
                "rules": [{"id": c, "shortDescription": {"text": CODES[c][1]},
                           "defaultConfiguration": {"level": CODES[c][0]}} for c in used],
            }},
            "results": results,
        }],
    }
    return json.dumps(doc, indent=2) + "\n"


FORMATS = {"text": format_text, "json": format_json, "sarif": format_sarif}
