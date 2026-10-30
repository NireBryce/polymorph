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


def check_shape(fx: object) -> list[str]:
    if not isinstance(fx, dict):
        return ["fixture must be an object"]
    errs = [f"missing key {k!r}" for k in sorted(FIXTURE_KEYS - fx.keys())]
    errs += [f"unknown key {k!r}" for k in sorted(fx.keys() - FIXTURE_KEYS)]
    for k in ("native_in", "canonical_in"):
        if k in fx and not isinstance(fx[k], dict):
            errs.append(f"{k} must be an object")
    if "hook_answer" in fx and fx["hook_answer"] is not None and not isinstance(fx["hook_answer"], dict):
        errs.append("hook_answer must be an object or null")
    no = fx.get("native_out")
    if "native_out" in fx:
        if not isinstance(no, dict) or "exit" not in no or "stdout" not in no:
            errs.append("native_out must be an object with stdout and exit")
        elif not isinstance(no["exit"], int) or isinstance(no["exit"], bool):
            errs.append("native_out.exit must be an integer")
    return errs


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
        from polymorph import adapter  # lazy: owned by another worker
    except ImportError as e:
        return 0, [f"adapter unavailable ({e}); fixtures not run"]
    passed, failures = 0, []
    for f in fixture_files(harness, root):
        label = f"{f.parent.name}/{f.stem}"
        try:
            fx = json.loads(f.read_text())
        except ValueError as e:
            failures.append(f"{label}: bad JSON: {e}")
            continue
        errs = check_shape(fx)
        if errs:
            failures.append(f"{label}: shape: {'; '.join(errs)}")
            continue
        try:
            with open(root / "mappings" / f"{f.parent.name}.toml", "rb") as mf:
                mapping = tomllib.load(mf)
            got_in = adapter.translate_in(mapping, fx["native_in"])
            bad = []
            if got_in != fx["canonical_in"]:
                bad.append(f"translate_in: want {fx['canonical_in']!r}, got {got_in!r}")
            if fx["hook_answer"] is not None:
                out = adapter.translate_out(mapping, fx["hook_answer"])
                want = fx["native_out"]
                if out.get("exit") != want["exit"]:
                    bad.append(f"exit: want {want['exit']!r}, got {out.get('exit')!r}")
                if not _same_stdout(want["stdout"], out.get("stdout")):
                    bad.append(f"stdout: want {want['stdout']!r}, got {out.get('stdout')!r}")
        except Exception as e:  # report, never crash the run
            failures.append(f"{label}: {type(e).__name__}: {e}")
            continue
        if bad:
            failures.append(f"{label}: " + "; ".join(bad))
        else:
            passed += 1
    return passed, failures
