"""`polymorph capture`: run the discovery procedure against a harness by itself.

Makes a scratch directory, installs the discovery logger into it through a driver
(capture_drivers.py), runs the fixed steps below through the harness's headless mode, and
writes a summary, a draft mapping and a report from the log. Then removes the logger and,
unless --keep, the scratch directory.

Rules this module keeps:
- The prompts and commands are the constants below and the driver's fixed argv. Nothing read
  from a log, a mapping or a harness reply is ever used as a prompt, a command or a path.
- The harness runs with cwd fixed to the scratch directory and a minimal environment: PATH,
  HOME and XDG_* (so it finds its own config), nothing else. The environment is never logged
  and the harness's authentication configuration is never read.
- Nothing runs without a typed `yes` (or --yes, which still prints a notice), and not at all on a
  non-interactive stdin without --yes. The default answer is no.
- The report lists what the log recorded. The harness's own account of what it did is never
  captured: its stdout and stderr go to /dev/null.
"""
from __future__ import annotations

import argparse
import datetime
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import __version__, discover
from .capture_drivers import DRIVERS, Driver, quoted

SCRATCH_PARENT = "polymorph-capture"   # under the system temp dir; polymorph owns it
DEFAULT_STEP_TIMEOUT = 120.0
DEFAULT_TOTAL_TIMEOUT = 600.0
SETTLE_SECONDS = 0.6   # detached loggers write after the step returns: wait for the count to stop moving
ENV_KEEP_EXACT = ("PATH", "HOME")
ENV_KEEP_PREFIX = ("XDG_",)

# The fixed steps: name, prompt. Constants; nothing is ever appended to them.
STEPS: tuple[tuple[str, str], ...] = (
    ("shell", "Run the shell command `echo hello`, then stop."),
    ("file", "Create a file named scratch.txt containing the single line hello, read it back, "
             "then delete it. Then stop."),
    ("search", "Search the current directory for the word hello and for file names containing "
               "scratch, then stop."),
    ("background", "Run the shell command `sleep 2; echo done` as a background command, wait "
                   "for it to finish, then stop."),
)
STEP_NAMES = tuple(n for n, _ in STEPS)

PROMPT_KIT = (
    ("Prompt 1: read its own documentation, read-only.",
     """\
You are running in the directory SCRATCH. Do not change any file and do not run any
command in this step. Read HARNESS's own documentation or installed source (not
third-party write-ups) and answer, quoting the file or page for each point: (a) which
hook or plugin events exist for tool calls, before and after; (b) where its hook or
plugin configuration is read from and in what format; (c) how a hook receives the tool
name and input; (d) how a hook blocks a tool call; (e) whether a changed hook file is
picked up without a restart. Write "not found" for anything the documentation does not
say. Do not guess.
"""),
    ("Prompt 2: write the logger in the scratch directory, show it, do not run it.",
     """\
Still in SCRATCH, still changing nothing outside it. Write the smallest hook or plugin
that, for every tool call, pipes the event as one JSON object to the command
`POLYMORPH discover log --out LOG` on standard input and does nothing else: it never
throws, never blocks or alters the call, never prints, and never writes the payload
anywhere except that command. Put it where HARNESS loads it from for this directory
only, not a global location. Show me the complete file and the path, and stop. Do not
run anything yet.
"""),
    ("Prompt 3: run canned steps, then summary and draft.",
     """\
Do these in order in SCRATCH and stop after the last: (1) run `echo hello` in the shell;
(2) create `scratch.txt` with one line, read it back, then delete it; (3) search the
directory for the word "hello"; (4) run `sleep 2; echo done` as a background command.
Then run `POLYMORPH discover summary LOG` and `POLYMORPH discover draft LOG --name
HARNESS`, and show both outputs unedited. Then list which of steps 1-4 produced no line
in the log.
"""),
)


def _drivers_text() -> str:
    return "\n".join(f"  {d.name:<10} {d.summary}" for d in DRIVERS.values())


def _epilog() -> str:
    untested = [d for d in DRIVERS.values() if d.untested]
    lines = ["drivers:", _drivers_text()]
    for d in untested:
        lines.append(f"\nUNTESTED: the {d.name} driver {d.untested}.")
    lines.append("\nA harness with no driver: `polymorph capture --print-prompts` prints the "
                 "prompt kit to paste by hand.")
    return "\n".join(lines)


def build_parser(sub=None) -> argparse.ArgumentParser:
    kw = dict(description=("Run a real harness through fixed steps in a scratch directory and write a "
                           "summary, a draft mapping and a report. It spends model credits and "
                           "auto-approves the harness's tool calls. COST: it runs a real AI model on your account and "
                           "spends tokens (money or plan usage); see README.md, \"Cost and responsibility\"."),
              epilog=_epilog(), formatter_class=argparse.RawDescriptionHelpFormatter)
    p = sub.add_parser("capture", help="run the discovery procedure against a harness, headless, in a scratch directory", **kw) if sub is not None else argparse.ArgumentParser(prog="polymorph capture", **kw)
    p.add_argument("--harness", help="driver name (see below); required unless --print-prompts")
    p.add_argument("--yes", action="store_true", help="skip the typed confirmation (required when stdin is not a terminal)")
    p.add_argument("--keep", action="store_true", help="keep the scratch directory afterwards")
    p.add_argument("--steps", nargs="+", metavar="STEP", help=f"run only these steps, in the fixed order: {', '.join(STEP_NAMES)}")
    p.add_argument("--step-timeout", type=float, default=DEFAULT_STEP_TIMEOUT, metavar="SECONDS")
    p.add_argument("--total-timeout", type=float, default=DEFAULT_TOTAL_TIMEOUT, metavar="SECONDS")
    p.add_argument("--out", default=None, metavar="DIR", help="results directory (default ./polymorph-capture-NAME; must be absent or empty)")
    p.add_argument("--print-prompts", action="store_true", help="print the prompt kit for a harness with no driver, run nothing, exit 0")
    p.add_argument("--full", action="store_true", help="log raw payloads (commands, paths, contents); default is redacted")
    return p


def print_prompts(out) -> None:
    print("Prompt kit for a harness `polymorph capture` has no driver for. Paste the prompts "
          "one at a time, in a scratch directory, reading each answer first.\n"
          "Replace: HARNESS = harness name, SCRATCH = an empty directory, LOG = the log file "
          "inside it, POLYMORPH = `<python> -m polymorph`.\n"
          "Guard rails: docs/discovery.md, \"Prompt kit\".\n", file=out)
    for title, text in PROMPT_KIT:
        print(f"{title}:\n", file=out)
        print("\n".join("> " + line for line in text.splitlines()) + "\n", file=out)


# ---- environment ------------------------------------------------------------

def minimal_env(src=None) -> dict[str, str]:
    """PATH, HOME and XDG_* from `src` (default: the process environment), nothing else."""
    src = os.environ if src is None else src
    return {k: v for k, v in src.items() if k in ENV_KEEP_EXACT or k.startswith(ENV_KEEP_PREFIX)}


# ---- running a step ---------------------------------------------------------

def run_step(argv: list[str], cwd: Path, env: dict, timeout: float) -> str:
    """Run one headless step; returns 'ok', 'exit N', 'timeout' or 'not started: ...'.

    The harness's output goes nowhere: only its exit status is kept. The process group is
    killed on timeout and on any exception (Ctrl-C included).
    """
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
    except OSError as e:
        return f"not started: {type(e).__name__}"
    try:
        try:
            rc = proc.wait(timeout=max(timeout, 0.01))
        except subprocess.TimeoutExpired:
            return "timeout"
        return "ok" if rc == 0 else f"exit {rc}"
    finally:
        if proc.poll() is None:
            _kill_group(proc)
        else:
            # the step may have left background children in its group
            _kill_group(proc, wait=False)


def _kill_group(proc, wait: bool = True) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if wait:
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def _line_count(log: Path) -> int:
    try:
        return len(log.read_bytes().splitlines())
    except OSError:
        return 0


def settled_count(log: Path) -> int:
    """Line count once it stops moving: the loggers are spawned detached and write late."""
    last = _line_count(log)
    quiet_since = time.monotonic()
    while time.monotonic() - quiet_since < SETTLE_SECONDS:
        time.sleep(0.05)
        now = _line_count(log)
        if now != last:
            last, quiet_since = now, time.monotonic()
    return last


def harness_version(driver: Driver, env: dict, cwd: Path) -> str:
    """First line of `<harness> --version`, cut and made printable; the text is untrusted."""
    try:
        r = subprocess.run(driver.version_argv(), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=15, errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(could not run --version: {type(e).__name__})"
    line = (r.stdout.strip() or r.stderr.strip()).splitlines()[:1]
    text = "".join(c if c.isprintable() else "?" for c in (line[0] if line else ""))
    return text[:100].replace("`", "'") or "(no output)"


# ---- the report -------------------------------------------------------------

def tool_calls(log: Path) -> dict[str, dict]:
    """tool name -> {n, keys: {input key -> count}} from the log's tool events. Names and keys only."""
    if not log.exists():
        return {}
    recs, _ = discover._load_records(log)
    sel, _ = discover._select(recs)
    table: dict[str, dict] = {}
    for _, rec in sel:
        _, tn, _ = discover._top(rec)
        b = table.setdefault(discover._safe(tn) or "<invalid>", {"n": 0, "keys": {}})
        b["n"] += 1
        for k in rec.get("tool_input_keys") or []:
            k = discover._safe(str(k), discover._KEY_RE) or "<invalid key>"
            b["keys"][k] = b["keys"].get(k, 0) + 1
    return table


def build_report(driver: Driver, version: str, results: list[dict], log: Path, full: bool,
                 started: str) -> str:
    calls = tool_calls(log)
    lines = ["# polymorph capture report", "",
             f"- polymorph version: {__version__}",
             f"- harness: {driver.name}",
             f"- harness `--version`: `{version}`",
             f"- captured: {started}",
             f"- log: {'RAW (--full): contains commands and file contents, do not share' if full else 'redacted'}"]
    if driver.untested:
        lines.append(f"- driver status: UNTESTED ({driver.untested})")
    lines += ["", "## Steps", "", "| step | result | log lines |", "|---|---|---|"]
    for r in results:
        lines.append(f"| {r['name']} | {r['status']} | {r['lines']} |")
    silent = [r["name"] for r in results if r["lines"] == 0 and r["status"] != "skipped"]
    lines += ["", "## Tool calls in the log", ""]
    if calls:
        lines += ["| tool | count | input keys (present/count) |", "|---|---|---|"]
        for tn, b in sorted(calls.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
            ks = ", ".join(f"{k} {n}/{b['n']}" for k, n in sorted(b["keys"].items())) or "-"
            lines.append(f"| {tn} | {b['n']} | {ks} |")
    else:
        lines.append("No tool events were logged.")
    lines += ["", "## Steps that produced no log line", ""]
    lines.append(", ".join(silent) if silent else "none")
    lines += ["", "This report is made from the discovery log only. The harness's own replies were not "
              "captured. The draft mapping is unverified: review it against the harness's documentation "
              "(skill `new-mapping`).", ""]
    return "\n".join(lines)


def write_results(out: Path, driver: Driver, log: Path, report: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if log.exists():
        shutil.copyfile(log, out / "log.jsonl")
        (out / "summary.txt").write_text(discover.summarize(log), encoding="utf-8")
        try:
            draft = discover.draft_mapping(log, driver.name)
        except Exception as e:   # a log with no usable lines
            draft = f"# no draft: {type(e).__name__}: {e}\n"
        (out / "draft.toml").write_text(draft, encoding="utf-8")
    else:
        (out / "summary.txt").write_text("no log was written: no hook or plugin call reached the logger\n")
        (out / "draft.toml").write_text("# no draft: no log was written\n")
    (out / "report.md").write_text(report, encoding="utf-8")


# ---- confirmation -----------------------------------------------------------

RULE = "=" * 72


def _bold(text: str, stdout) -> str:
    on = getattr(stdout, "isatty", lambda: False)() and not os.environ.get("NO_COLOR")
    return f"\033[1m{text}\033[0m" if on else text


def warning_block(driver: Driver, steps, scratch: Path, full: bool, step_timeout: float,
                  total_timeout: float, stdout=None) -> str:
    n = len(steps)
    head = _bold(f"WARNING: THIS RUNS A REAL AI MODEL {n} TIME{'S' if n != 1 else ''} AND SPENDS TOKENS", stdout)
    lines = [RULE, head, RULE,
             f"polymorph capture will run `{driver.binary}` {n} time(s), once per step.",
             "Every step spends tokens, and a step may take several model turns.",
             "  - If the harness is billed per token (an API key), this costs real money.",
             "  - If it is on a subscription plan (for example a coding plan), it counts against",
             "    that plan's usage limits and may be billed as extra usage.",
             "polymorph cannot tell which billing applies and cannot measure the cost.",
             "If you are not willing to pay, or are unsure what your plan bills, cancel now.", "",
             "Scope (not a cost; the cost is unknown):",
             f"  model-driven steps: {n} ({', '.join(nm for nm, _ in steps)})",
             f"  timeouts: {step_timeout:g}s per step, {total_timeout:g}s in total",
             f"  harness: {driver.name}  ({quoted(driver.step_argv('PROMPT'))})",
             f"  scratch dir: {scratch}  (created by polymorph; the working directory of every step)",
             f"  log: {'RAW (--full)' if full else 'redacted'}", "",
             "Tool calls are auto-approved inside the scratch directory. It is the working",
             "directory, not a sandbox: an auto-approved tool call can still reach files outside it.",
             "The prompts are:"]
    lines += [f"  {nm}: {pr}" for nm, pr in steps]
    lines.append(RULE)
    return "\n".join(lines) + "\n"


def short_notice(driver: Driver, steps) -> str:
    n = len(steps)
    return (f"NOTICE: running {driver.binary} {n} time(s) with tool calls auto-approved; this spends "
            "tokens (per-token billing costs money; a subscription plan counts against its limits).\n"
            "--yes given: not asking.\n")


def confirm(stdin, out, text: str) -> bool:
    out.write(text + '\nContinue? Type "yes" to run, anything else cancels [no]: ')
    out.flush()
    return stdin.readline().strip().lower() == "yes"


# ---- main -------------------------------------------------------------------

def run(args, stdin=None, stdout=None, stderr=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    if args.print_prompts:
        print_prompts(stdout)
        return 0
    if not args.harness or args.harness not in DRIVERS:
        what = f"unknown harness {args.harness!r}" if args.harness else "--harness is required"
        print(f"polymorph capture: {what}. Known drivers:\n{_drivers_text()}\n"
              "For any other harness use `polymorph capture --print-prompts`.", file=stderr)
        return 2
    driver = DRIVERS[args.harness]
    names = args.steps or list(STEP_NAMES)
    bad = [n for n in names if n not in STEP_NAMES]
    if bad:
        print(f"polymorph capture: unknown step {bad[0]!r}; steps are {', '.join(STEP_NAMES)}", file=stderr)
        return 2
    steps = [(n, p) for n, p in STEPS if n in names]
    env = minimal_env()
    if shutil.which(driver.binary, path=env.get("PATH", "")) is None:
        print(f"polymorph capture: {driver.binary!r} is not on PATH", file=stderr)
        return 2
    out_dir = Path(args.out or f"polymorph-capture-{driver.name}").resolve()
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        print(f"polymorph capture: {out_dir} exists and is not an empty directory", file=stderr)
        return 2
    interactive = stdin.isatty()
    if not interactive and not args.yes:
        print("polymorph capture: stdin is not a terminal; refusing without --yes", file=stderr)
        return 2

    parent = Path(tempfile.gettempdir()) / SCRATCH_PARENT
    parent.mkdir(mode=0o700, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f"{driver.name}-", dir=parent)).resolve()
    keep = args.keep
    try:
        block = warning_block(driver, steps, scratch, args.full, args.step_timeout,
                              args.total_timeout, stdout)
        if args.yes:
            stdout.write(short_notice(driver, steps))
        elif not confirm(stdin, stdout, block):
            print("cancelled: nothing was run and no scratch directory was left. "
                  "`polymorph capture --print-prompts` prints a manual procedure; "
                  "`--steps` runs fewer steps.", file=stdout)
            return 0
        return _capture(args, driver, steps, scratch, out_dir, env, stdout, stderr)
    except KeyboardInterrupt:
        print("interrupted; cleaning up", file=stderr)
        return 130
    finally:
        try:
            driver.remove(scratch)
        finally:
            if keep:
                print(f"kept scratch directory {scratch} (remove it yourself)", file=stderr)
            else:
                shutil.rmtree(scratch, ignore_errors=True)
                try:
                    parent.rmdir()   # only succeeds when no other capture is using it
                except OSError:
                    pass


def _capture(args, driver, steps, scratch, out_dir, env, stdout, stderr) -> int:
    started = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log = driver.log_path(scratch)
    driver.install(scratch, args.full)
    version = harness_version(driver, env, scratch)
    results: list[dict] = []
    deadline = time.monotonic() + args.total_timeout
    try:
        for name, prompt in steps:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                results.append({"name": name, "status": "skipped", "lines": 0})
                continue
            before = _line_count(log)
            status = run_step(driver.step_argv(prompt), scratch, env, min(args.step_timeout, remaining))
            after = settled_count(log)
            results.append({"name": name, "status": status, "lines": after - before})
            print(f"{name}: {status}, {after - before} log lines", file=stdout)
    finally:
        driver.remove(scratch)   # the logger goes even if a step raised or was interrupted
    report = build_report(driver, version, results, log, args.full, started)
    write_results(out_dir, driver, log, report)
    print(f"results: {out_dir} (summary.txt, draft.toml, report.md, log.jsonl)", file=stdout)
    if args.full:
        print("WARNING: --full: log.jsonl holds raw commands and file contents; do not share it", file=stderr)
    bad = [r for r in results if r["status"] != "ok"]
    return 1 if bad else 0


def main(argv: list[str] | None = None, stdin=None, stdout=None, stderr=None) -> int:
    return run(build_parser().parse_args(argv), stdin, stdout, stderr)
