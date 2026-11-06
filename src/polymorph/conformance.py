"""Fixture conformance: replay fixtures/<harness>/<case>.json through the adapter.

The adapter module is imported lazily so validation and fixture-shape checks work
without it. Fixture `native_out.stdout` is either a JSON value (compared parsed)
or a string (compared verbatim); an empty/None stdout means "no output".
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

from .resources import DATA_ROOT

FIXTURE_KEYS = {"native_in", "canonical_in", "hook_answer", "native_out"}


def fixture_files(harness: str | None = None, root: Path = DATA_ROOT) -> list[Path]:
    base = root / "fixtures"
    dirs = [base / harness] if harness else sorted(p for p in base.iterdir() if p.is_dir())
    return [f for d in dirs if d.is_dir() for f in sorted(d.glob("*.json"))]


def check_shape(fixture: object) -> list[str]:
    if not isinstance(fixture, dict):
        return ["fixture must be an object"]
    errors = [f"missing key {k!r}" for k in sorted(FIXTURE_KEYS - fixture.keys())]
    errors += [f"unknown key {k!r}" for k in sorted(fixture.keys() - FIXTURE_KEYS)]
    for k in ("native_in", "canonical_in"):
        if k in fixture and not isinstance(fixture[k], dict):
            errors.append(f"{k} must be an object")
    if "hook_answer" in fixture and fixture["hook_answer"] is not None and not isinstance(fixture["hook_answer"], dict):
        errors.append("hook_answer must be an object or null")
    native_out = fixture.get("native_out")
    if "native_out" in fixture:
        if not isinstance(native_out, dict) or "exit" not in native_out or "stdout" not in native_out:
            errors.append("native_out must be an object with stdout and exit")
        elif not isinstance(native_out["exit"], int) or isinstance(native_out["exit"], bool):
            errors.append("native_out.exit must be an integer")
        if isinstance(native_out, dict) and "stderr" in native_out and not isinstance(native_out["stderr"], str):
            errors.append("native_out.stderr must be a string when present")
    return errors


def _same_stdout(want, got) -> bool:
    if want in (None, ""):
        return got in (None, "")
    if isinstance(want, str):
        return got == want
    try:
        return json.loads(got) == want
    except (TypeError, ValueError):
        return False


def run_fixtures(harness: str | None = None, root: Path = DATA_ROOT):
    """Return (passed, failures); failures is a list of strings."""
    try:
        from polymorph import adapter  # lazy: so shape checks work without the adapter installed
    except ImportError as e:
        return 0, [f"adapter unavailable ({e}); fixtures not run"]
    passed, failures = 0, []
    for fixture_path in fixture_files(harness, root):
        label = f"{fixture_path.parent.name}/{fixture_path.stem}"
        try:
            fixture = json.loads(fixture_path.read_text())
        except ValueError as e:
            failures.append(f"{label}: bad JSON: {e}")
            continue
        errors = check_shape(fixture)
        if errors:
            failures.append(f"{label}: shape: {'; '.join(errors)}")
            continue
        try:
            with open(root / "mappings" / f"{fixture_path.parent.name}.toml", "rb") as mapping_file:
                mapping = tomllib.load(mapping_file)
            got_in = adapter.translate_in(mapping, fixture["native_in"])
            mismatches = []
            if got_in != fixture["canonical_in"]:
                mismatches.append(f"translate_in: want {fixture['canonical_in']!r}, got {got_in!r}")
            out = adapter.translate_out(mapping, fixture["hook_answer"])
            want = fixture["native_out"]
            if out.get("exit") != want["exit"]:
                mismatches.append(f"exit: want {want['exit']!r}, got {out.get('exit')!r}")
            if not _same_stdout(want["stdout"], out.get("stdout")):
                mismatches.append(f"stdout: want {want['stdout']!r}, got {out.get('stdout')!r}")
            if "stderr" in want and out.get("stderr") != want["stderr"]:
                mismatches.append(f"stderr: want {want['stderr']!r}, got {out.get('stderr')!r}")
        except Exception as e:  # report, never crash the run
            failures.append(f"{label}: {type(e).__name__}: {e}")
            continue
        if mismatches:
            failures.append(f"{label}: " + "; ".join(mismatches))
        else:
            passed += 1
    return passed, failures
