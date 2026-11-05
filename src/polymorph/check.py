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
from .decisions import DECISIONS_DIR, load_decisions
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
    "direct-ask-unenforceable": ("warning", "A direct-delivery mapping sets ask = false: no adapter runs, so nothing sets GUARD_ASK_ENFORCED and the hook cannot observe the claim (issue #34)."),
    "deny-unenforceable": ("warning", "An adapter-delivery mapping has no way to express a deny (no payload_out.deny; exit_2_blocks and json_answers both false), so a guard deny is a NOT-checked fault and the call proceeds (issue #35)."),
    "exit2-unhonoured": ("warning", "A mapping's deny or ask route renders as exit 2 while exit_2_blocks is false: the exit the route relies on is the one the mapping says does not block (issue #49)."),
    "hook-outside-hooks-dir": ("error", "A hook command is not a bare script under .agents/hooks/."),
    "hook-missing": ("error", "A hook script does not exist."),
    "hook-not-executable": ("error", "A hook script is not executable."),
    "decision-undecided": ("error", "A hook needs an event, tool class or field the target lacks and no decision answers it."),
    "decision-weakens-guard": ("warning", "A decision runs a hook with less than it needs (run-empty, or a different event class)."),
    "decision-stale": ("warning", "A decision's id matches no mismatch now."),
    "decision-invalid": ("error", "A decision file or entry fails validation."),
    "setting-unmapped": ("note", "A setting has no cross-harness meaning."),
    "generated-drift": ("error", "A target's generated file differs from what generate would write."),
    "loadout-drift": ("warning", "The mapping bytes equip consumed have changed since it ran (or the file is gone); the adapter re-reads mappings at run time."),
    "loadout-invalid": ("error", "loadout.toml fails validation."),
    "fixtures-missing": ("warning", "A built-in mapping has no conformance fixtures."),
    "fixtures-skipped": ("note", "Fixtures are not checked for a repo-local mapping."),
    "fixture-failed": ("error", "A conformance fixture does not pass."),
    "mapping-override": ("note", "A repo-local mapping overrides a built-in of the same name."),
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
    hooks = data.get("hooks") or {}
    if not isinstance(hooks, dict):
        return [_f("settings-invalid", None, f"{SETTINGS}: hooks must be an object", SETTINGS)]
    for event, groups in hooks.items():
        if not isinstance(groups, list) or not all(
                isinstance(group, dict) and isinstance(group.get("hooks", []), list)
                and all(isinstance(hook, dict) for hook in group.get("hooks", [])) for group in groups):
            return out + [_f("settings-invalid", None,
                             f"{SETTINGS}: hooks.{event} must be a list of matcher groups, each with a list of hook objects",
                             SETTINGS)]
        for group in groups:
            for hook in group.get("hooks", []):
                if hook.get("type", "command") != "command":
                    continue
                command = hook.get("command", "")
                try:
                    script_path = resolve_script(command, repo)
                except ReaderError as e:
                    out.append(_f("hook-outside-hooks-dir", None, f"{event}: {e}", SETTINGS))
                    continue
                path = repo / script_path
                if not path.is_file():
                    out.append(_f("hook-missing", None, f"{event}: {script_path} does not exist", script_path))
                elif not os.access(path, os.X_OK):
                    out.append(_f("hook-not-executable", None, f"{event}: {script_path} is not executable", script_path))
    return out


def check_mapping(repo: Path, name: str, mapping: Mapping) -> list[Finding]:
    out = [_f("mapping-invalid", name, error, _rel(repo, mapping.source)) for error in validate_mapping(mapping.source)]
    if not mapping.verified:
        out.append(_f("mapping-unverified", name, "mapping has no `verified` field; treat output as unverified",
                      _rel(repo, mapping.source)))
    out.append(_f("mapping-untested-live", name, "not tested live: block semantics, ask enforcement unproven"))
    # claims a hook cannot observe or enforce, from the delivery the mapping chose (issues #34, #35)
    if mapping.delivery == "direct" and mapping.capabilities.get("ask") is False:
        out.append(_f("direct-ask-unenforceable", name,
                      'delivery = "direct" runs the hook bare: no adapter runs, so nothing sets '
                      "GUARD_ASK_ENFORCED and the hook cannot see the ask = false claim; the hardening "
                      "works only in a script written to detect this harness itself", _rel(repo, mapping.source)))
    if mapping.delivery == "adapter" and not mapping.payload_out.get("deny") \
            and not mapping.capabilities.get("exit_2_blocks") and not mapping.capabilities.get("json_answers"):
        out.append(_f("deny-unenforceable", name,
                      "no payload_out.deny and both exit_2_blocks and json_answers are false: a guard "
                      "deny becomes a visible NOT-checked fault and the call proceeds, so this mapping "
                      "cannot enforce a block", _rel(repo, mapping.source)))
    if mapping.delivery == "adapter" and (mapping.payload_out.get("deny") or mapping.payload_out.get("ask")) \
            and not mapping.capabilities.get("exit_2_blocks"):
        out.append(_f("exit2-unhonoured", name,
                      "the payload_out deny/ask route renders as exit 2 while exit_2_blocks is false: "
                      "the exit the route relies on is the one the mapping says does not block, so the "
                      "call would proceed", _rel(repo, mapping.source)))
    return out


def check_fixtures(repo: Path, name: str, mapping: Mapping) -> list[Finding]:
    """Built-in mappings only: replay fixtures/<name>/ through the adapter."""
    if Path(mapping.source).resolve().parent != BUILTIN_DIR.resolve():
        return [_f("fixtures-skipped", name, "repo-local mapping: fixtures not checked", _rel(repo, mapping.source))]
    if not fixture_files(name):
        return [_f("fixtures-missing", name, f"no fixtures under fixtures/{name}/")]
    _, failures = run_fixtures(name)
    return [_f("fixture-failed", name, msg) for msg in failures]


def check_loadout(repo: Path, name: str) -> list[Finding]:
    """Provenance: the mapping bytes equip consumed still match the file it pinned.

    The adapter re-reads the mapping at run time, so artifact swap after equip weakens
    the guard with no other trace; the pin in loadout.toml makes it visible.
    """
    import hashlib

    from .equip import EquipError, manifest_entries

    try:
        entries = manifest_entries(repo, name)
    except EquipError as e:
        return [_f("loadout-invalid", name, str(e))]
    out: list[Finding] = []
    for artifact in entries:
        if not (artifact.mapping_path and artifact.mapping_hash):
            continue
        mapping_file = Path(artifact.mapping_path)
        if not mapping_file.is_file():
            out.append(_f("loadout-drift", name,
                          f"the mapping equip consumed is gone: {artifact.mapping_path}"))
        elif "sha256:" + hashlib.sha256(mapping_file.read_bytes()).hexdigest() != artifact.mapping_hash:
            out.append(_f("loadout-drift", name,
                          f"the mapping bytes equip consumed have changed: {artifact.mapping_path}"))
    return out


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
                   ".polymorph/basecamp/config.toml")]

    findings += check_hook_scripts(repo)
    try:
        canon = read_claude(repo)
    except ReaderError:
        canon = None  # already reported by check_hook_scripts

    for name in names:
        try:
            mapping = load_mapping(name, repo,
                             warn=lambda msg: findings.append(_f("mapping-override", name, msg)))
        except MappingError as e:
            findings.append(_f("mapping-invalid", name, str(e)))
            continue
        findings += check_mapping(repo, name, mapping)
        findings += check_fixtures(repo, name, mapping)
        findings += check_loadout(repo, name)
        if canon is None:
            continue
        decisions_file = f"{DECISIONS_DIR}/{name}.toml"
        decisions, decision_errors = load_decisions(repo, name, set(mapping.events.values()))
        findings += [_f("decision-invalid", name, e, decisions_file) for e in decision_errors]
        try:
            target_result = generate_target(repo, canon, mapping, decisions)
        except (ValueError, OSError) as e:
            findings.append(_f("generated-drift", name, f"cannot compute generated file: {e}", mapping.config_path))
            continue
        for mismatch in target_result.undecided:
            findings.append(_f("decision-undecided", name, f"{mismatch.id}: {mismatch.detail}; options: {' | '.join(mismatch.options)}",
                               decisions_file))
        findings += [_f("decision-weakens-guard", name, f"{applied.id} = {applied.choice}: {applied.text}", decisions_file)
                     for applied in target_result.applied if applied.weakening]
        findings += [_f("decision-stale", name, f"{stale_id} matches no mismatch now", decisions_file) for stale_id in target_result.stale]
        findings += [_f("setting-unmapped", name, unmapped_setting) for unmapped_setting in target_result.unmapped]
        if target_result.drift:
            state = "missing" if not (repo / target_result.path).is_file() else "differs from generate output"
            findings.append(_f("generated-drift", name, f"{target_result.path} {state}; run `polymorph generate`", target_result.path))
    return findings


def summarize(findings: list[Finding]) -> dict[str, int]:
    return {summary: sum(1 for finding in findings if finding.severity == summary) for summary in SEVERITIES}


def exit_code(findings: list[Finding], strict: bool = False) -> int:
    summary = summarize(findings)
    return 1 if summary["error"] or (strict and summary["warning"]) else 0


# --- formats ----------------------------------------------------------------

def format_text(findings: list[Finding]) -> str:
    lines: list[str] = []
    groups: dict[str | None, list[Finding]] = {}
    for finding in findings:
        groups.setdefault(finding.target, []).append(finding)
    for target in sorted(groups, key=lambda target_name: (target_name is not None, target_name or "")):
        lines.append(f"[{target}]" if target else "[repo]")
        for finding in groups[target]:
            where = f" ({finding.file})" if finding.file else ""
            lines.append(f"  {finding.severity} {finding.code} {target or '-'}: {finding.message}{where}")
    summary = summarize(findings)
    lines.append(f"{summary['error']} error(s), {summary['warning']} warning(s), {summary['note']} note(s)")
    return "\n".join(lines) + "\n"


def format_json(findings: list[Finding]) -> str:
    return json.dumps({"findings": [asdict(finding) for finding in findings], "summary": summarize(findings)}, indent=2) + "\n"


def format_sarif(findings: list[Finding]) -> str:
    used = sorted({finding.code for finding in findings})
    results = []
    for finding in findings:
        sarif_result: dict = {
            "ruleId": finding.code,
            "level": finding.severity,
            "message": {"text": f"{finding.target}: {finding.message}" if finding.target else finding.message},
        }
        if finding.file:
            sarif_result["locations"] = [{"physicalLocation": {"artifactLocation": {"uri": finding.file}}}]
        results.append(sarif_result)
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "polymorph",
                "rules": [{"id": code, "shortDescription": {"text": CODES[code][1]},
                           "defaultConfiguration": {"level": CODES[code][0]}} for code in used],
            }},
            "results": results,
        }],
    }
    return json.dumps(doc, indent=2) + "\n"


FORMATS = {"text": format_text, "json": format_json, "sarif": format_sarif}
