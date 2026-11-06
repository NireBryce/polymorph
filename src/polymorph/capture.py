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
- A step's process group is killed only after the harness exits and the log has settled (the
  plugin's logger children live in that group and write late), or at once on a timeout or signal.
- An interrupted run still writes its results (the report marks the step that was cut and
  the steps not run), so the tokens already spent are not lost.
- The report lists what the log recorded. The harness's own account of what it did is never
  captured: its stdout and stderr go to /dev/null.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import math
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

DEFAULT_STEP_TIMEOUT = 120.0
DEFAULT_TOTAL_TIMEOUT = 600.0
SETTLE_SECONDS = 0.6   # loggers write after the harness exits: wait for the line count to stop moving
SETTLE_CAP = 10.0      # ...and for the harness's process group to empty, but never longer than this
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
    return "\n".join(f"  {driver.name:<10} {driver.summary}" for driver in DRIVERS.values())


def _epilog() -> str:
    untested = [driver for driver in DRIVERS.values() if driver.untested]
    lines = ["drivers:", _drivers_text()]
    for driver in untested:
        lines.append(f"\nUNTESTED: the {driver.name} driver {driver.untested}.")
    lines.append("\nA harness with no driver: `polymorph capture --print-prompts` prints the "
                 "prompt kit to paste by hand.")
    return "\n".join(lines)


def build_parser(sub=None) -> argparse.ArgumentParser:
    parser_kwargs = dict(description=("Run a real harness through fixed steps in a scratch directory and write a "
                           "summary, a draft mapping and a report. It spends model credits and "
                           "auto-approves the harness's tool calls. COST: it runs a real AI model on your account and "
                           "spends tokens (money or plan usage); see README.md, \"Cost and responsibility\"."),
              epilog=_epilog(), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser = sub.add_parser("capture", help="run the discovery procedure against a harness, headless, in a scratch directory", **parser_kwargs) if sub is not None else argparse.ArgumentParser(prog="polymorph capture", **parser_kwargs)
    parser.add_argument("--harness", help="driver name (see below); required unless --print-prompts")
    parser.add_argument("--yes", action="store_true", help="skip the typed confirmation (required when stdin is not a terminal)")
    parser.add_argument("--keep", action="store_true", help="keep the scratch directory afterwards")
    parser.add_argument("--steps", nargs="+", metavar="STEP", help=f"run only these steps, in the fixed order: {', '.join(STEP_NAMES)}")
    parser.add_argument("--step-timeout", type=float, default=DEFAULT_STEP_TIMEOUT, metavar="SECONDS",
                   help="kill a step after this many seconds (default %(default)s)")
    parser.add_argument("--total-timeout", type=float, default=DEFAULT_TOTAL_TIMEOUT, metavar="SECONDS",
                   help="skip the remaining steps after this many seconds (default %(default)s)")
    parser.add_argument("--out", default=None, metavar="DIR", help="results directory (default ./polymorph-capture-NAME; must be absent or empty)")
    parser.add_argument("--print-prompts", action="store_true", help="print the prompt kit for a harness with no driver, run nothing, exit 0")
    parser.add_argument("--full", action="store_true",
                   help="log raw payloads (commands, paths, contents); default is redacted. Needs --out "
                        "and the long flag below, then asks you to type yes")
    parser.add_argument("--i-really-want-this-in-the-repo-raw", "--i-really-want-this-in-the-repo",
                   dest="raw_in_repo", action="store_true",
                   help="required with --full: accept a plain-text raw log (then type yes when asked)")
    return parser


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

def run_step(argv: list[str], cwd: Path, env: dict, timeout: float, log: Path | None = None) -> str:
    """Run one headless step; returns 'ok', 'exit N', 'timeout' or 'not started: ...'.

    The harness's output goes nowhere: only its exit status is kept. When the harness exits
    by itself, its process group is NOT killed at once: the plugin's logger children are in
    that group (spawned unref'd, not detached) and write after the harness is gone, so the
    step waits until no group member is alive and `log` has stopped growing (capped by
    SETTLE_CAP) and only then kills whatever is left. On a timeout, an exception or a signal
    (Ctrl-C, SIGTERM, SIGHUP) the group is killed immediately.
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
        if log is not None:
            settled_count(log, group=proc.pid)
        return "ok" if rc == 0 else f"exit {rc}"
    finally:
        _kill_group(proc)


def _kill_group(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _line_count(log: Path) -> int:
    try:
        return len(log.read_bytes().splitlines())
    except OSError:
        return 0


def settled_count(log: Path, group: int | None = None) -> int:
    """Line count once it stops moving for SETTLE_SECONDS and, given `group` (a process group
    id), once no process in that group is alive; gives up after SETTLE_CAP seconds."""
    last = _line_count(log)
    start = quiet_since = time.monotonic()
    while True:
        time.sleep(0.05)
        current_count = _line_count(log)
        if current_count != last:
            last, quiet_since = current_count, time.monotonic()
        now = time.monotonic()
        if now - start >= SETTLE_CAP:
            return last
        if now - quiet_since >= SETTLE_SECONDS and not (group is not None and _group_alive(group)):
            return last


def harness_version(driver: Driver, env: dict, cwd: Path) -> str:
    """First line of `<harness> --version`, cut and made printable; the text is untrusted."""
    try:
        result = subprocess.run(driver.version_argv(), cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=15, errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(could not run --version: {type(e).__name__})"
    first_lines = (result.stdout.strip() or result.stderr.strip()).splitlines()[:1]
    text = "".join(char if char.isprintable() else "?" for char in (first_lines[0] if first_lines else ""))
    return text[:100].replace("`", "'") or "(no output)"


# ---- the report -------------------------------------------------------------

def tool_calls(log: Path) -> dict[str, dict]:
    """tool name -> {n, keys: {input key -> count}} from the log's tool events. Names and keys only."""
    if not log.exists():
        return {}
    recs, _, _ = discover._load_records(log)
    selected, _ = discover._select(recs)
    table: dict[str, dict] = {}
    for _, rec in selected:
        _, tool_name, _ = discover._top(rec)
        bucket = table.setdefault(discover._safe(tool_name) or "<invalid>", {"n": 0, "keys": {}})
        bucket["n"] += 1
        for k in rec.get("tool_input_keys") or []:
            k = discover._safe(str(k), discover._KEY_RE) or "<invalid key>"
            bucket["keys"][k] = bucket["keys"].get(k, 0) + 1
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
    for result in results:
        lines.append(f"| {result['name']} | {result['status']} | {result['lines']} |")
    silent = [result["name"] for result in results if result["lines"] == 0 and result["status"] not in ("skipped", "not run")]
    lines += ["", "## Tool calls in the log", ""]
    if calls:
        lines += ["| tool | count | input keys (present/count) |", "|---|---|---|"]
        for tool_name, bucket in sorted(calls.items(), key=lambda item: (-item[1]["n"], item[0])):
            keys_text = ", ".join(f"{k} {n}/{bucket['n']}" for k, n in sorted(bucket["keys"].items())) or "-"
            lines.append(f"| {tool_name} | {bucket['n']} | {keys_text} |")
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
    step_count = len(steps)
    head = _bold(f"WARNING: THIS RUNS A REAL AI MODEL {step_count} TIME{'S' if step_count != 1 else ''} AND SPENDS TOKENS", stdout)
    lines = [RULE, head, RULE,
             f"polymorph capture will run `{driver.binary}` {step_count} time(s), once per step.",
             "Every step spends tokens, and a step may take several model turns.",
             "  - If the harness is billed per token (an API key), this costs real money.",
             "  - If it is on a subscription plan (for example a coding plan), it counts against",
             "    that plan's usage limits and may be billed as extra usage.",
             "polymorph cannot tell which billing applies and cannot measure the cost.",
             "If you are not willing to pay, or are unsure what your plan bills, cancel now.", "",
             "Scope (not a cost; the cost is unknown):",
             f"  model-driven steps: {step_count} ({', '.join(step_name for step_name, _ in steps)})",
             f"  timeouts: {step_timeout:g}s per step, {total_timeout:g}s in total",
             f"  harness: {driver.name}  ({quoted(driver.step_argv('PROMPT'))})",
             f"  scratch dir: {scratch}  (created by polymorph; the working directory of every step)",
             f"  log: {'RAW (--full)' if full else 'redacted'}", "",
             "Tool calls are auto-approved inside the scratch directory. It is the working",
             "directory, not a sandbox: an auto-approved tool call can still reach files outside it.",
             "The prompts are:"]
    lines += [f"  {step_name}: {prompt}" for step_name, prompt in steps]
    lines.append(RULE)
    return "\n".join(lines) + "\n"


def short_notice(driver: Driver, steps) -> str:
    step_count = len(steps)
    return (f"NOTICE: running {driver.binary} {step_count} time(s) with tool calls auto-approved; this spends "
            "tokens (per-token billing costs money; a subscription plan counts against its limits).\n"
            "--yes given: not asking.\n")


def confirm(stdin, out, text: str) -> bool:
    out.write(text + '\nContinue? Type "yes" to run, anything else cancels [no]: ')
    out.flush()
    return stdin.readline().strip().lower() == "yes"


# ---- signals ----------------------------------------------------------------

class Interrupted(KeyboardInterrupt):
    """A SIGINT, SIGTERM or SIGHUP, handled like Ctrl-C: cleanup runs, then `code` is the exit status."""

    def __init__(self, code: int = 130):
        super().__init__()
        self.code = code


@contextlib.contextmanager
def interrupt_on_signals():
    """For the duration of the run, SIGTERM and SIGHUP raise `Interrupted` like Ctrl-C does.

    The harness runs in its own session, so nothing else would stop it when polymorph is
    killed. Only the first signal raises; a later one is ignored. If the first lands inside
    the cleanup, `_run_confirmed` runs the cleanup again, and nothing can interrupt that pass.
    The previous handlers are restored afterwards. Off the main thread (where signal handlers
    cannot be set) this does nothing.
    """
    exit_codes_by_signal = {signal.SIGINT: 130, signal.SIGTERM: 143, signal.SIGHUP: 129}
    fired = False

    def handler(signum, frame):
        nonlocal fired
        if fired:
            return
        fired = True
        raise Interrupted(exit_codes_by_signal[signum])

    previous = {}
    try:
        for sig in exit_codes_by_signal:
            previous[sig] = signal.signal(sig, handler)
    except ValueError:   # not the main thread
        pass
    try:
        yield
    finally:
        for sig, old in previous.items():
            signal.signal(sig, old)


# ---- main -------------------------------------------------------------------

def _out_problem(out_dir: Path) -> str | None:
    """Why `out_dir` cannot be created or written, found before any step spends tokens."""
    existing = out_dir
    while not existing.exists():
        if existing.parent == existing:
            break
        existing = existing.parent
    if not existing.is_dir():
        return f"{existing} is not a directory"
    if not os.access(existing, os.W_OK | os.X_OK):
        return f"{existing} is not writable"
    return None


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
    try:
        discover.require_out_for_full(getattr(args, "full", False), getattr(args, "out", None) is not None, "--out")
    except ValueError as e:
        print(f"polymorph capture: {e}", file=stderr)
        return 2
    driver = DRIVERS[args.harness]
    if driver.blocked:
        print(f"polymorph capture: the {driver.name} driver is blocked: {driver.blocked}.", file=stderr)
        return 2
    requested_names = args.steps or list(STEP_NAMES)
    unknown_steps = [step_name for step_name in requested_names if step_name not in STEP_NAMES]
    if unknown_steps:
        print(f"polymorph capture: unknown step {unknown_steps[0]!r}; steps are {', '.join(STEP_NAMES)}", file=stderr)
        return 2
    steps = [(step_name, prompt) for step_name, prompt in STEPS if step_name in requested_names]
    env = minimal_env()
    if shutil.which(driver.binary, path=env.get("PATH", "")) is None:
        print(f"polymorph capture: {driver.binary!r} is not on PATH", file=stderr)
        return 2
    for flag, value in (("--step-timeout", args.step_timeout), ("--total-timeout", args.total_timeout)):
        if not math.isfinite(value) or value <= 0:
            print(f"polymorph capture: {flag} must be a finite number of seconds above 0, got {value!r}",
                  file=stderr)
            return 2
    out_dir = Path(args.out or f"polymorph-capture-{driver.name}").resolve()
    problem = _out_problem(out_dir)
    if problem:
        print(f"polymorph capture: --out {out_dir}: {problem}", file=stderr)
        return 2
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        print(f"polymorph capture: {out_dir} exists and is not an empty directory", file=stderr)
        return 2
    if args.full:
        try:
            discover.confirm_full(out_dir, args.raw_in_repo, "--out", stdin, stdout)
        except ValueError as e:
            print(f"polymorph capture: {e}", file=stderr)
            return 2
    interactive = stdin.isatty()
    if not interactive and not args.yes:
        print("polymorph capture: stdin is not a terminal; refusing without --yes", file=stderr)
        return 2

    with interrupt_on_signals():
        return _run_confirmed(args, driver, steps, env, out_dir, stdin, stdout, stderr)


def _run_confirmed(args, driver, steps, env, out_dir, stdin, stdout, stderr) -> int:
    state = {"scratch": None, "keep": args.keep, "said_kept": False}
    try:
        try:
            return _confirm_and_capture(args, driver, steps, env, out_dir, stdin, stdout, stderr, state)
        except KeyboardInterrupt as e:
            print("interrupted; cleaning up", file=stderr)
            return getattr(e, "code", 130)
        finally:
            _cleanup(driver, state, stderr)
    except KeyboardInterrupt as e:
        # The first signal landed in the `finally` cleanup above, which may have stopped part
        # way. Only the first signal raises (interrupt_on_signals), so this pass runs to the end.
        print("interrupted during cleanup; cleaning up", file=stderr)
        _cleanup(driver, state, stderr)
        return getattr(e, "code", 130)


def _cleanup(driver, state, stderr) -> None:
    """Remove the logger, then the scratch directory unless kept. Safe to run twice: each step
    ignores what is already gone, and the 'kept' line prints once."""
    scratch = state["scratch"]
    if scratch is None:
        return
    try:
        driver.remove(scratch)
    finally:
        if not state["keep"]:
            shutil.rmtree(scratch, ignore_errors=True)
        elif not state["said_kept"]:
            state["said_kept"] = True
            print(f"kept scratch directory {scratch} (remove it yourself)", file=stderr)


def _confirm_and_capture(args, driver, steps, env, out_dir, stdin, stdout, stderr, state) -> int:
    # mkdtemp makes a fresh 0700 directory with an unpredictable name directly in the system
    # temp dir: no shared, guessable parent to pre-create, and nothing for concurrent runs to race on
    scratch = state["scratch"] = Path(tempfile.mkdtemp(prefix=f"polymorph-capture-{driver.name}-")).resolve()
    block = warning_block(driver, steps, scratch, args.full, args.step_timeout,
                          args.total_timeout, stdout)
    if args.yes:
        stdout.write(short_notice(driver, steps))
    else:
        try:
            ok = confirm(stdin, stdout, block)
        except KeyboardInterrupt:
            state["keep"] = False   # nothing was run; the directory goes, so do not claim to keep it
            raise
        if not ok:
            state["keep"] = False   # nothing was run; the directory goes, so do not claim to keep it
            print("cancelled: nothing was run and no scratch directory was left. "
                  "`polymorph capture --print-prompts` prints a manual procedure; "
                  "`--steps` runs fewer steps.", file=stdout)
            return 0
    return _capture(args, driver, steps, scratch, out_dir, env, stdout, stderr)


def _capture(args, driver, steps, scratch, out_dir, env, stdout, stderr) -> int:
    started = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log = driver.log_path(scratch)
    driver.install(scratch, args.full)
    version = harness_version(driver, env, scratch)
    results: list[dict] = []
    deadline = time.monotonic() + args.total_timeout
    interruption: KeyboardInterrupt | None = None
    try:
        for name, prompt in steps:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                results.append({"name": name, "status": "skipped", "lines": 0})
                continue
            before = _line_count(log)
            try:
                status = run_step(driver.step_argv(prompt), scratch, env,
                                  min(args.step_timeout, remaining), log)
            except KeyboardInterrupt:
                results.append({"name": name, "status": "interrupted", "lines": _line_count(log) - before})
                raise
            after = _line_count(log)   # run_step already waited for the log to settle
            results.append({"name": name, "status": status, "lines": after - before})
            print(f"{name}: {status}, {after - before} log lines", file=stdout)
    except KeyboardInterrupt as caught:
        # Tokens already spent are not thrown away: what the log holds is written out below.
        # Only the first signal raises (interrupt_on_signals), so this write is not cut short.
        interruption = caught
        finished_names = {result["name"] for result in results}
        results += [{"name": name, "status": "not run", "lines": 0}
                    for name, _ in steps if name not in finished_names]
    finally:
        driver.remove(scratch)   # the logger goes even if a step raised or was interrupted
    report = build_report(driver, version, results, log, args.full, started)
    write_results(out_dir, driver, log, report)
    listed = "summary.txt, draft.toml, report.md" + (", log.jsonl" if log.exists() else "")
    print(f"results: {out_dir} ({listed})", file=stdout)
    if interruption is not None:
        print("interrupted: the results above are partial results", file=stdout)
        raise interruption
    if args.full:
        print("WARNING: --full: log.jsonl holds raw commands and file contents; do not share it", file=stderr)
    failed_results = [result for result in results if result["status"] != "ok"]
    return 1 if failed_results else 0


def main(argv: list[str] | None = None, stdin=None, stdout=None, stderr=None) -> int:
    return run(build_parser().parse_args(argv), stdin, stdout, stderr)
