"""polymorph command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .canon import Canon  # noqa: F401  (re-export convenience)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="polymorph")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manage", help="maintainer half: keep the basecamp and generate harness configs")
    ms = m.add_subparsers(dest="mcmd", required=True)

    mg = ms.add_parser("generate", help="write harness configs from .agents/settings.json")
    mg.add_argument("--repo", default=".")
    mg.add_argument("--harness", action="append", default=None)
    mg.add_argument("--check", action="store_true", help="exit 1 on drift, write nothing")
    mg.add_argument("--mappings", default=None)
    mg.add_argument("--trust-dir", action="append", default=[])

    mk = ms.add_parser("check", help="static analysis of the repo's agent config (runs nothing, writes nothing)")
    mk.add_argument("--repo", default=".")
    mk.add_argument("--harness", action="append", default=None)
    mk.add_argument("--format", choices=["text", "json", "sarif"], default="text")
    mk.add_argument("--strict", action="store_true", help="warnings also fail the run")

    mv = ms.add_parser("validate", help="validate a mapping file")
    mv.add_argument("file")

    mc = ms.add_parser("conformance", help="run fixtures against mappings")
    mc.add_argument("--harness", default=None)

    md = ms.add_parser("adopt", help="move agent files at rest into the basecamp store and symlink them back")
    md.add_argument("--repo", default=".")
    md.add_argument("--dry-run", action="store_true", help="report what would move; write nothing")

    q = sub.add_parser("equip", help="the onboarding run: write the loadout and report honoured surface, gaps and the env contract")
    q.add_argument("--repo", default=".")
    q.add_argument("--harness", required=True)
    q.add_argument("--dry-run", action="store_true", help="write nothing; report what equip would do")
    q.add_argument("--gitignore", action="store_true",
                   help="contributor posture: append the marked regenerable-artifacts section to .gitignore")

    a = sub.add_parser("adapter", help="run a hook script through a harness adapter")
    a.add_argument("--harness", required=True)
    a.add_argument("--mappings", default=None)
    a.add_argument("--trust-dir", action="append", default=[])
    a.add_argument("script", nargs="+", help="after `--`: hook script (and args)")

    i = sub.add_parser("inventory", help="the repo's discover-and-report surface and each harness's gaps")
    i.add_argument("--repo", default=".")
    i.add_argument("--harness", action="append", default=None, metavar="NAME",
                   help="coverage tables to report (default: every table found)")
    i.add_argument("--json", dest="as_json", action="store_true", help="machine-readable output")

    e = sub.add_parser("env", help="emit the env contract from .polymorph/basecamp/env.toml, or run a command under it")
    e.add_argument("--repo", default=".")
    e.add_argument("--harness", default=None, metavar="NAME", help="harness whose session markers to emit")
    e.add_argument("--exec", dest="exec_cmd", default=None, metavar="CMD",
                   help="run CMD under sh -c with the contract applied, and exit with its status")

    from .capture import build_parser as _capture_parser
    _capture_parser(sub)

    d = sub.add_parser("discover", help="log a harness's hook payloads and draft a mapping")
    ds = d.add_subparsers(dest="dcmd", required=True)
    di = ds.add_parser("init", help="add a catch-all logger hook to a config, keeping the hooks already there")
    di.add_argument("--harness", required=True)
    di.add_argument("--config-path", required=True)
    di.add_argument("--event-name", default="PreToolUse")
    di.add_argument("--matcher", default="*")
    di.add_argument("--shape", default="claude-settings", choices=["claude-settings", "claude-like"],
                    help="claude-settings: hooks.<Event> (Claude Code, its SDK); claude-like: ZCode")
    di.add_argument("--log-dir", default=None, help="absolute log directory (default ./.polymorph-discovery)")
    di.add_argument("--env-be-very-careful", dest="env", action="store_true", help="WARNING: makes the logger read the process environment and record the NAMES (never values) of PROJECT/WORKSPACE/HARNESS-like variables, visibly baked into the hook command")
    di.add_argument("--full", action="store_true", help="log raw payloads (commands, paths, contents); default is redacted")
    dl = ds.add_parser("log", help="append stdin to a JSONL log (always exits 0, silent)")
    dl.add_argument("--out", required=True)
    dl.add_argument("--env-be-very-careful", dest="env", action="store_true", help="log names (never values) of PROJECT/WORKSPACE/HARNESS-like env vars")
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
    dd = ds.add_parser("draft", help="print a draft mapping from a discovery log")
    dd.add_argument("--name", required=True)
    for sp in (dm, dd):
        sp.add_argument("file")
        sp.add_argument("--include", action="append", default=[], metavar="EVENT",
                        help="also use lines of this event (repeatable); default is tool events only")
        sp.add_argument("--event", action="append", default=[], metavar="EVENT",
                        help="use only lines of this event (repeatable), replacing the tool-event default")
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
        print(discover.summarize(args.file, args.include, args.event), end="")
        return 0
    if args.dcmd == "reload-check":
        if args.finish:
            seen, msg = discover.reload_check_finish(args.config_path, args.scratch_log)
            print(msg)
            return 0 if seen else 1
        print(discover.reload_check_start(args.config_path, args.scratch_log, args.event_name,
                                          args.matcher, shape=args.shape))
        return 0
    print(discover.draft_mapping(args.file, args.name, args.include, args.event), end="")
    return 0


def _cmd_generate(args) -> int:
    from .generate import generate

    results = generate(args.repo, args.harness, args.check, args.mappings, args.trust_dir)
    if not results:
        print("no targets: pass --harness or list them in .polymorph/basecamp/config.toml", file=sys.stderr)
        return 2
    from .decisions import format_question

    drift = False
    pending = 0
    for r in results:
        if r.undecided:
            pending += len(r.undecided)
            print(f"[{r.harness}] NOT WRITTEN {r.path}: {len(r.undecided)} undecided")
        else:
            state = "DRIFT" if r.drift else "ok"
            verb = "would write" if (args.check and r.drift) else ("wrote" if r.drift else "unchanged")
            print(f"[{r.harness}] {verb} {r.path} ({state})")
        for w in r.warnings:
            print(f"  warning: {w}")
        for mm in r.undecided:
            for line in format_question(r.harness, mm, r.native_events):
                print(line)
        for a in r.applied:
            print(f"  {'weakened' if a.weakening else 'decided'}: {a.id} = {a.choice}: {a.text}")
        for sid in r.stale:
            print(f"  warning: stale decision {sid} (matches nothing now)")
        for n in r.notes:
            print(f"  note: {n}")
        for u in r.unmapped:
            print(f"  unmapped setting: {u}")
        drift = drift or r.drift
    if pending:
        print(f"{pending} undecided item(s): nothing written for the targets above; answer them and run again")
        return 3
    return 1 if (args.check and drift) else 0


def _cmd_capture(args) -> int:
    from . import capture

    return capture.run(args)


def _cmd_inventory(args) -> int:
    from . import inventory

    repo = Path(args.repo)
    inv = inventory.read_inventory(repo)
    names = args.harness if args.harness else inventory.available_tables(repo)
    tables: dict[str, dict | None] = {}
    gaps: list[dict] = []
    for name in names:
        cap = inventory.load_coverage(name, repo)
        if cap is None:
            tables[name] = None
            continue
        tables[name] = {"skills": cap.skills, "rules": cap.rules,
                        "memory": list(cap.memory), "claude_dir": cap.claude_dir}
        for g in inventory.gaps(inv, cap):
            gaps.append({"harness": g.harness, "kind": g.kind, "path": g.path})
    if args.as_json:
        import json

        print(json.dumps({
            "inventory": {kind: [{"path": i.path, "target": i.target} for i in inv.kind(kind)]
                          for kind in inventory.KINDS},
            "gaps": gaps,
        }, indent=2))
        return 0
    for kind in inventory.KINDS:
        entries = inv.kind(kind)
        print(f"{kind}: {len(entries)}")
        for i in entries:
            suffix = f" -> {i.target}" if i.target else ""
            print(f"  - {i.path}{suffix}")
    for name in names:
        if tables[name] is None:
            print(f"gaps for {name}: no coverage table; nothing is known")
            continue
        found = [g for g in gaps if g["harness"] == name]
        print(f"gaps for {name}: {len(found)}")
        for g in found:
            print(f"  - {g['kind']} {g['path']}")
    return 0


def _cmd_adopt(args) -> int:
    from .adopt import adopt

    r = adopt(args.repo, args.dry_run)
    verb = "would move" if args.dry_run else "moved"
    for a in r.moved:
        print(f"{verb} [{a.kind}] {a.path} -> {a.store}")
    for kind, path, points in r.already:
        print(f"already [{kind}] {path} -> {points}")
    for kind, path in r.absent:
        print(f"absent [{kind}] {path}")
    for kind, path, reason in r.refused:
        print(f"refused [{kind}] {path}: {reason}")
    n = len(r.moved)
    print(f"{n} moved, {len(r.already)} already adopted, {len(r.absent)} absent, {len(r.refused)} refused")
    return 1 if r.refused else 0


def _cmd_env(args) -> int:
    from .env import contract_lines, load_env, run_exec

    if args.exec_cmd is not None:
        return run_exec(args.repo, args.harness, args.exec_cmd)
    facts = load_env(args.repo)
    if args.harness is not None and args.harness not in facts.harness:
        from .env import _harness_known

        if not _harness_known(Path(args.repo), args.harness, facts):
            raise ValueError(f"unknown harness {args.harness!r}: no [harness.{args.harness}] in env.toml and no mapping")
    print("\n".join(contract_lines(facts, args.harness)))
    return 0


def _cmd_equip(args) -> int:
    from .equip import equip

    r = equip(args.repo, args.harness, args.dry_run, args.gitignore)
    verb = "would write" if args.dry_run else "wrote"
    for p in r.written:
        print(f"[{r.harness}] {verb} {p}")
    for p in r.unchanged:
        print(f"[{r.harness}] unchanged {p}")
    for p in r.not_written:
        print(f"[{r.harness}] NOT WRITTEN {p}: {r.undecided} undecided (polymorph manage generate prints the questions)")
    if r.coverage_known:
        print(f"[{r.harness}] honoured surface:")
        for kind, path in r.honoured:
            print(f"  - {kind} {path}")
        print(f"[{r.harness}] gaps (will be silently ignored): {len(r.gaps)}")
        for kind, path in r.gaps:
            print(f"  - {kind} {path}")
    else:
        print(f"[{r.harness}] coverage: no coverage table; nothing is known about what this harness honours")
    if r.env_lines is None:
        print(f"[{r.harness}] env: no env.toml; no env contract emitted")
    else:
        print(f"[{r.harness}] env contract:")
        for line in r.env_lines:
            print(f"  {line}")
    if r.manifest_written:
        print(f"[{r.harness}] manifest: .polymorph/basecamp/loadout.toml")
    print(f"[{r.harness}] posture: {r.posture}")
    if r.gitignore_suggested and not r.gitignore_appended:
        print(f"[{r.harness}] gitignore: issued artifacts are regenerable; pass --gitignore to append:")
        for line in r.gitignore_suggested:
            print(f"  {line}")
    if r.gitignore_appended:
        print(f"[{r.harness}] gitignore: appended the marked regenerable-artifacts section")
    return 0


def _cmd_check(args) -> int:
    from . import check

    findings = check.run_check(args.repo, args.harness)
    sys.stdout.write(check.FORMATS[args.format](findings))
    return check.exit_code(findings, args.strict)


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
    if args.cmd == "manage":
        handler = {"generate": _cmd_generate, "check": _cmd_check, "validate": _cmd_validate,
                   "conformance": _cmd_conformance, "adopt": _cmd_adopt}[args.mcmd]
    else:
        handler = {
            "adapter": _cmd_adapter, "discover": _cmd_discover, "capture": _cmd_capture,
            "inventory": _cmd_inventory, "env": _cmd_env, "equip": _cmd_equip,
        }[args.cmd]
    try:
        return handler(args)
    except Exception as e:  # visible, non-zero, never silent
        if e.__class__.__module__.startswith("polymorph") or isinstance(e, (ValueError, OSError)):
            print(f"polymorph: error: {e}", file=sys.stderr)
            return 2
        raise
