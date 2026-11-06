"""Decisions: recorded answers to generation-time mismatches.

When a target harness lacks an event, a tool class or a payload field that a hook needs,
`generate` neither guesses nor drops the hook silently: the item is *undecided* until
`.polymorph/basecamp/decisions/NAME.toml` answers it. The file is data, validated here and
never executed.
"""

from __future__ import annotations

import datetime
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .canon import EVENTS

DECISIONS_DIR = ".polymorph/basecamp/decisions"
SCHEMA = 2   # schema 1 ids were <hook-script>:<kind>; the event dimension was added in schema 2

# kind -> allowed choices ("substitute" takes ":<native event>")
KINDS = {
    "no-event": ("skip", "substitute"),
    "no-tool-class": ("skip",),
    "missing-field": ("skip", "run-empty"),
}
# id = <hook-script>:<canonical event>:<kind>: a script registered under several events gets
# one mismatch (and one answer) per event, so a shared script's decision no longer answers
# every event it is registered under (issue #37)
_ID = re.compile(r"^(?P<script>[^:\s]+):(?P<event>[a-z_]+):(?P<kind>[a-z-]+)$")
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
    def event(self) -> str:
        return self.id.split(":")[1]

    @property
    def kind(self) -> str:
        return self.id.rsplit(":", 1)[1]


@dataclass(frozen=True)
class Mismatch:
    """One thing the target lacks that a hook needs. id = `<hook-script>:<event>:<kind>`."""
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
    """Valid entries and one error string per problem; a invalid entry is dropped, the rest kept."""
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
    for decision_id, entry in table.items():
        match = _ID.match(decision_id)
        if not match or match["kind"] not in KINDS:
            errs.append(f"decision id {decision_id!r} must be <hook-script>:<event>:<kind>, "
                        f"event one of {', '.join(EVENTS)}, kind one of {', '.join(KINDS)}")
            continue
        if match["event"] not in EVENTS:
            errs.append(f"decision id {decision_id!r}: event {match['event']!r} is not a canonical event "
                        f"({', '.join(EVENTS)})")
            continue
        if not isinstance(entry, dict):
            errs.append(f"{decision_id}: must be a table")
            continue
        invalid = False
        for k in sorted(set(entry) - ENTRY_KEYS):
            errs.append(f"{decision_id}: unknown key {k!r}")
            invalid = True
        choice, date, note = entry.get("choice"), entry.get("date"), entry.get("note")
        if not isinstance(choice, str):
            errs.append(f"{decision_id}: choice must be a string")
            invalid = True
        else:
            head, _, arg = choice.partition(":")
            if head not in KINDS[match["kind"]] or (arg and head != "substitute") or (head == "substitute" and not arg):
                errs.append(f"{decision_id}: choice {choice!r} is not one of {', '.join(options_for(match['kind'], native_events))}")
                invalid = True
            elif head == "substitute" and (not _NATIVE.match(arg) or arg not in native_events):
                errs.append(f"{decision_id}: {arg!r} is not a native event of this mapping "
                            f"({', '.join(sorted(native_events)) or 'none'})")
                invalid = True
        if isinstance(date, datetime.date):
            date = date.isoformat()
        try:
            if not isinstance(date, str) or len(date) != 10:
                raise ValueError
            datetime.date.fromisoformat(date)
        except ValueError:
            errs.append(f"{decision_id}: date must be YYYY-MM-DD")
            invalid = True
        if note is not None and not isinstance(note, str):
            errs.append(f"{decision_id}: note must be a string")
            invalid = True
        if not invalid:
            out[decision_id] = Decision(decision_id, choice, date, note)
    return out, errs


def load_decisions(repo: Path, name: str, native_events: set[str]) -> tuple[dict[str, Decision], list[str]]:
    """(decisions, errors). A missing file is no decisions and no errors."""
    path = decisions_path(repo, name)
    if not path.is_file():
        return {}, []
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        return {}, [f"cannot read: {e}"]
    return parse_decisions(raw, native_events)


def options_for(kind: str, native_events: set[str] | None = None) -> tuple[str, ...]:
    out = []
    for choice in KINDS[kind]:
        if choice == "substitute":
            out.append("substitute:<native event>")
        else:
            out.append(choice)
    return tuple(out)


def format_question(target: str, mismatch: Mismatch, native_events: list[str]) -> list[str]:
    """One undecided item as a fixed-shape block, readable by an agent relaying it to its user."""
    options_text = " | ".join(mismatch.options)
    if "substitute:<native event>" in mismatch.options:
        options_text += f"   (native events: {', '.join(native_events)})"
    classes_text = f", classes {','.join(mismatch.classes)}" if mismatch.classes else ""
    return [
        f"  question: {mismatch.id}",
        f"    hook:    {mismatch.script} ({mismatch.event}{classes_text})",
        f"    problem: {mismatch.detail}",
        f"    options: {options_text}",
        "    default: skip",
        f'    answer:  in {DECISIONS_DIR}/{target}.toml add [decisions."{mismatch.id}"] with choice = "<option>", date = "YYYY-MM-DD"',
    ]
