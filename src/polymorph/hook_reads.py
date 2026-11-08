"""Declared payload reads: the fields a hook script reads beyond what its tool classes imply.

polymorph never reads hook scripts, so `generate` models what a hook needs by tool class
(`generate.CLASS_FIELDS`). A guard that also reads `tool_response` or `cwd` says so in
`.polymorph/basecamp/hook-reads.toml`, and a target whose `payload_in` does not map the
field raises the same `missing-field` question as a class field would. The file is data,
validated here and never executed:

    schema = 1

    [hooks.".agents/hooks/audit.sh"]
    reads = ["tool_response", "cwd"]
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from .canonical import CANON_PATHS

HOOK_READS_FILE = ".polymorph/basecamp/hook-reads.toml"
# the keys a mapping's payload_in may carry for a hook to read
READABLE_FIELDS = frozenset(CANON_PATHS) | {"tool_response", "event"}


class HookReadsError(ValueError):
    pass


def parse_hook_reads(raw: object, known_scripts: set[str]) -> dict[str, tuple[str, ...]]:
    """Script -> declared fields. Raises HookReadsError listing every problem."""
    if not isinstance(raw, dict):
        raise HookReadsError("hook-reads file must be a table")
    errors = [f"unknown key {key!r}" for key in sorted(set(raw) - {"schema", "hooks"})]
    if raw.get("schema") != 1 or isinstance(raw.get("schema"), bool):
        errors.append(f"schema must be 1, got {raw.get('schema')!r}")
    table = raw.get("hooks", {})
    declared: dict[str, tuple[str, ...]] = {}
    if not isinstance(table, dict):
        raise HookReadsError("; ".join(errors + ["hooks must be a table"]))
    for script, entry in table.items():
        if script not in known_scripts:
            errors.append(f"{script}: matches no hook registered in .agents/settings.json")
        if not isinstance(entry, dict):
            errors.append(f"{script}: must be a table")
            continue
        errors += [f"{script}: unknown key {key!r}" for key in sorted(set(entry) - {"reads"})]
        reads = entry.get("reads")
        if not isinstance(reads, list) or not reads or not all(isinstance(field, str) for field in reads):
            errors.append(f"{script}: reads must be a non-empty list of field names")
            continue
        unknown = sorted(set(reads) - READABLE_FIELDS)
        if unknown:
            errors.append(f"{script}: not canonical field(s) {', '.join(unknown)} "
                          f"(known: {', '.join(sorted(READABLE_FIELDS))})")
            continue
        declared[script] = tuple(dict.fromkeys(reads))
    if errors:
        raise HookReadsError("; ".join(errors))
    return declared


def load_hook_reads(repo: Path, known_scripts: set[str]) -> dict[str, tuple[str, ...]]:
    """Declared reads by script; a missing file declares nothing."""
    path = Path(repo) / HOOK_READS_FILE
    if not path.is_file():
        return {}
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise HookReadsError(f"cannot read: {e}") from e
    return parse_hook_reads(raw, known_scripts)
