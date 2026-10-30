"""Decisions: recorded answers to generation-time mismatches.

When a target harness lacks an event, a tool class or a payload field that a hook needs,
`generate` neither guesses nor drops the hook silently: the item is *undecided* until
`.agents/polymorph/decisions/NAME.toml` answers it. The file is data, validated here and
never executed.
"""

from __future__ import annotations

import datetime
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

DECISIONS_DIR = ".agents/polymorph/decisions"
SCHEMA = 1

# kind -> allowed choices ("substitute" takes ":<native event>")
KINDS = {
    "no-event": ("skip", "substitute"),
    "no-tool-class": ("skip",),
    "missing-field": ("skip", "run-empty"),
}
_ID = re.compile(r"^(?P<script>[^:\s]+):(?P<kind>[a-z-]+)$")
_NATIVE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]*$")
ENTRY_KEYS = {"choice", "date", "note"}
TOP_KEYS = {"schema", "decisions"}


class DecisionError(ValueError):
    pass


@dataclass(frozen=True)
class Decision:
    id: str
    choice: str
    date: str
    note: str | None = None

    @property
    def kind(self) -> str:
        return self.id.rsplit(":", 1)[1]


@dataclass(frozen=True)
class Mismatch:
    """One thing the target lacks that a hook needs. id = `<hook-script>:<kind>`."""
    id: str
    kind: str
    script: str
    event: str
    classes: tuple[str, ...]
    detail: str
    options: tuple[str, ...]  # human-readable, e.g. "substitute:<native event>"


@dataclass(frozen=True)
class Applied:
    id: str
    choice: str
    weakening: bool
    text: str


def decisions_path(repo: Path, name: str) -> Path:
    return Path(repo) / DECISIONS_DIR / f"{name}.toml"


def parse_decisions(raw: object, native_events: set[str]) -> tuple[dict[str, Decision], list[str]]:
    """Valid entries and one error string per problem; a bad entry is dropped, the rest kept."""
    errs: list[str] = []
    out: dict[str, Decision] = {}
    if not isinstance(raw, dict):
        return out, ["decision file must be a table"]
    for k in sorted(set(raw) - TOP_KEYS):
        errs.append(f"unknown key {k!r}")
    if raw.get("schema") != SCHEMA or isinstance(raw.get("schema"), bool):
        errs.append(f"schema must be {SCHEMA}, got {raw.get('schema')!r}")
    table = raw.get("decisions", {})
    if not isinstance(table, dict):
        return out, errs + ["decisions must be a table"]
    for did, e in table.items():
        m = _ID.match(did)
        if not m or m["kind"] not in KINDS:
            errs.append(f"decision id {did!r} must be <hook-script>:<kind>, kind one of {', '.join(KINDS)}")
            continue
        if not isinstance(e, dict):
            errs.append(f"{did}: must be a table")
            continue
        bad = False
        for k in sorted(set(e) - ENTRY_KEYS):
            errs.append(f"{did}: unknown key {k!r}")
            bad = True
        choice, date, note = e.get("choice"), e.get("date"), e.get("note")
        if not isinstance(choice, str):
            errs.append(f"{did}: choice must be a string")
            bad = True
        else:
            head, _, arg = choice.partition(":")
            if head not in KINDS[m["kind"]] or (arg and head != "substitute") or (head == "substitute" and not arg):
                errs.append(f"{did}: choice {choice!r} is not one of {', '.join(options_for(m['kind'], native_events))}")
                bad = True
            elif head == "substitute" and (not _NATIVE.match(arg) or arg not in native_events):
                errs.append(f"{did}: {arg!r} is not a native event of this mapping "
                            f"({', '.join(sorted(native_events)) or 'none'})")
                bad = True
        if isinstance(date, datetime.date):
            date = date.isoformat()
        try:
            if not isinstance(date, str) or len(date) != 10:
                raise ValueError
            datetime.date.fromisoformat(date)
        except ValueError:
            errs.append(f"{did}: date must be YYYY-MM-DD")
            bad = True
        if note is not None and not isinstance(note, str):
            errs.append(f"{did}: note must be a string")
            bad = True
        if not bad:
            out[did] = Decision(did, choice, date, note)
    return out, errs


def load_decisions(repo: Path, name: str, native_events: set[str]) -> tuple[dict[str, Decision], list[str]]:
    """(decisions, errors). A missing file is no decisions and no errors."""
    p = decisions_path(repo, name)
    if not p.is_file():
        return {}, []
    try:
        raw = tomllib.loads(p.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        return {}, [f"cannot read: {e}"]
    return parse_decisions(raw, native_events)


def options_for(kind: str, native_events: set[str] | None = None) -> tuple[str, ...]:
    out = []
    for c in KINDS[kind]:
        if c == "substitute":
            out.append("substitute:<native event>")
        else:
            out.append(c)
    return tuple(out)


def format_question(target: str, mm: Mismatch, native_events: list[str]) -> list[str]:
    """One undecided item as a fixed-shape block, readable by an agent relaying it to its user."""
    opts = " | ".join(mm.options)
    if "substitute:<native event>" in mm.options:
        opts += f"   (native events: {', '.join(native_events)})"
    cls = f", classes {','.join(mm.classes)}" if mm.classes else ""
    return [
        f"  question: {mm.id}",
        f"    hook:    {mm.script} ({mm.event}{cls})",
        f"    problem: {mm.detail}",
        f"    options: {opts}",
        "    default: skip",
        f'    answer:  in {DECISIONS_DIR}/{target}.toml add [decisions."{mm.id}"] with choice = "<option>", date = "YYYY-MM-DD"',
    ]
