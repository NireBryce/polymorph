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

    d = sub.add_parser("discover", help="log a harness's hook payloads and draft a mapping")
    ds = d.add_subparsers(dest="dcmd", required=True)
    di = ds.add_parser("init", help="write a config whose only hook is a catch-all logger")
    di.add_argument("--harness", required=True)
    di.add_argument("--config-path", required=True)
    di.add_argument("--event-name", default="PreToolUse")
    di.add_argument("--matcher", default="*")
    di.add_argument("--shape", default="claude-settings", choices=["claude-settings", "claude-like"],
                    help="claude-settings: hooks.<Event> (Claude Code, its SDK); claude-like: ZCode")
    di.add_argument("--log-dir", default=None, help="absolute log directory (default ./.polymorph-discovery)")
    di.add_argument("--env", action="store_true", help="also log names (never values) of PROJECT/WORKSPACE/HARNESS-like env vars")
    di.add_argument("--full", action="store_true", help="log raw payloads (commands, paths, contents); default is redacted")
    dl = ds.add_parser("log", help="append stdin to a JSONL log (always exits 0, silent)")
    dl.add_argument("--out", required=True)
    dl.add_argument("--env", action="store_true", help="log names (never values) of PROJECT/WORKSPACE/HARNESS-like env vars")
    dl.add_argument("--full", action="store_true",
                    help="log raw payloads; default replaces values with {type, length}")
    dr = ds.add_parser("reload-check", help="test whether a running harness re-reads hook edits")
    dr.add_argument("--config-path", required=True)
    dr.add_argument("--scratch-log", required=True)
    dr.add_argument("--finish", action="store_true", help="report, then remove the temporary hook")
    dr.add_argument("--event-name", default="PreToolUse")
    dr.add_argument("--matcher", default="*")
    dr.add_argument("--shape", default=None, choices=["claude-settings", "claude-like"],
                    help="default: detected from the existing file")
    dm = ds.add_parser("summary", help="table of keys, tools and counts in a discovery log")
    dm.add_argument("file")
    dd = ds.add_parser("draft", help="print a draft mapping from a discovery log")
    dd.add_argument("file")
    dd.add_argument("--name", required=True)
    return p


def _cmd_discover(args) -> int:
    from . import discover

    if args.dcmd == "log":
        discover.log_stdin(args.out, redact=not args.full, env=args.env)
        return 0
    if args.dcmd == "init":
        p = discover.init_config(args.harness, args.config_path, args.event_name, args.matcher,
                                shape=args.shape, log_dir=args.log_dir, redact=not args.full, env=args.env)
        print(f"wrote {p}")
        return 0
    if args.dcmd == "summary":
        print(discover.summarize(args.file), end="")
        return 0
    if args.dcmd == "reload-check":
        if args.finish:
            seen, msg = discover.reload_check_finish(args.config_path, args.scratch_log)
            print(msg)
            return 0 if seen else 1
        print(discover.reload_check_start(args.config_path, args.scratch_log, args.event_name,
                                          args.matcher, shape=args.shape))
        return 0
    print(discover.draft_mapping(args.file, args.name), end="")
    return 0


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

    argv = ["--harness", args.harness]
    if args.mappings:
        argv += ["--mappings", args.mappings]
    for d in args.trust_dir:
        argv += ["--trust-dir", d]
    argv += ["--", *args.script]
    return int(run_adapter(argv) or 0)


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

    passed, failures = run_fixtures(args.harness)
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    print(f"{passed} passed, {len(failures)} failed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        # `discover log` must never block the harness, even on a bad command line
        if argv[:2] == ["discover", "log"]:
            return 0
        raise
    handler = {
        "generate": _cmd_generate, "adapter": _cmd_adapter,
        "validate": _cmd_validate, "discover": _cmd_discover, "conformance": _cmd_conformance,
    }[args.cmd]
    try:
        return handler(args)
    except Exception as e:  # visible, non-zero, never silent
        if e.__class__.__module__.startswith("polymorph") or isinstance(e, (ValueError, OSError)):
            print(f"polymorph: error: {e}", file=sys.stderr)
            return 2
        raise
