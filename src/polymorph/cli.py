"""polymorph command line."""

from __future__ import annotations

import argparse
import sys

from .canon import Canon  # noqa: F401  (re-export convenience)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="polymorph")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write harness configs from .agents/settings.json")
    g.add_argument("--repo", default=".")
    g.add_argument("--harness", action="append", default=None)
    g.add_argument("--check", action="store_true", help="exit 1 on drift, write nothing")
    g.add_argument("--mappings", default=None)
    g.add_argument("--trust-dir", action="append", default=[])

    a = sub.add_parser("adapter", help="run a hook script through a harness adapter")
    a.add_argument("--harness", required=True)
    a.add_argument("--mappings", default=None)
    a.add_argument("--trust-dir", action="append", default=[])
    a.add_argument("script", nargs="+", help="after `--`: hook script (and args)")

    v = sub.add_parser("validate", help="validate a mapping file")
    v.add_argument("file")

    c = sub.add_parser("conformance", help="run fixtures against mappings")
    c.add_argument("--harness", default=None)
    return p


def _cmd_generate(args) -> int:
    from .generate import generate

    results = generate(args.repo, args.harness, args.check, args.mappings, args.trust_dir)
    if not results:
        print("no targets: pass --harness or list them in .agents/harnesses.toml", file=sys.stderr)
        return 2
    drift = False
    for r in results:
        state = "DRIFT" if r.drift else "ok"
        verb = "would write" if (args.check and r.drift) else ("wrote" if r.drift else "unchanged")
        print(f"[{r.harness}] {verb} {r.path} ({state})")
        for w in r.warnings:
            print(f"  warning: {w}")
        for gap in r.gaps:
            print(f"  gap: {gap}")
        for u in r.unmapped:
            print(f"  unmapped setting: {u}")
        drift = drift or r.drift
    return 1 if (args.check and drift) else 0


def _cmd_adapter(args) -> int:
    from .adapter import run_adapter

    rc = run_adapter(args.harness, args.script, mappings_dir=args.mappings)
    return int(rc or 0)


def _cmd_validate(args) -> int:
    from .validate import validate_mapping

    errors = validate_mapping(args.file)
    if errors:
        for e in (errors if isinstance(errors, (list, tuple)) else [errors]):
            print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{args.file}: ok")
    return 0


def _cmd_conformance(args) -> int:
    from .conformance import run_fixtures

    rc = run_fixtures(args.harness)
    return int(rc or 0)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(argv)
    handler = {
        "generate": _cmd_generate, "adapter": _cmd_adapter,
        "validate": _cmd_validate, "conformance": _cmd_conformance,
    }[args.cmd]
    try:
        return handler(args)
    except Exception as e:  # visible, non-zero, never silent
        if e.__class__.__module__.startswith("polymorph") or isinstance(e, (ValueError, OSError)):
            print(f"polymorph: error: {e}", file=sys.stderr)
            return 2
        raise
