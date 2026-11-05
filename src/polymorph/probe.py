"""`polymorph discover probe`: observe a harness's capability flags in a live run.

`[capabilities]` cannot be read from payloads, so a draft defaults it to all false and
the flags stay claims (docs/limits.md). A probe drives a scripted session -- one
synthetic marker command per decision route -- through a harness that a capture driver
can run headlessly, with a guard hook wired beside the discovery logger. The guard (a
shipped template, constants written in at render time) acts only on commands carrying
this run's random token: it answers per case -- allow, exit-2 deny, JSON deny, JSON ask,
input rewrite -- and writes one decode line per dispatched case. The decode file joins
to the discovery log on the call id, and the interpreter fills the five capability
booleans together with the way each was observed, derived or left untested. The probe
can only ever block its own synthetic commands, never real ones.

Nothing from a mapping, a discovery log or a harness reply becomes code or a command:
the guard is shipped template code driven by constants only, and the step prompts are
constants with the token formatted in. `verified` is never written by a probe.
"""
from __future__ import annotations

import datetime
import random
import shlex
import shutil
import string
import sys
import tempfile
import time
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from . import discover
from .capture import (DRIVERS, RULE, Driver, _bold, _drivers_text, _line_count, confirm,
                      harness_version, interrupt_on_signals, minimal_env, run_step, short_notice)
from .resources import DATA_DIR
from .validate import CAPS, validate_mapping

GUARD_TEMPLATE = "probe-guard-hook.py"
GUARD_CONSTANTS_MARK = "#@@CONSTANTS@@#"
DEFAULT_STEP_TIMEOUT = 120.0
DEFAULT_TOTAL_TIMEOUT = 300.0
GUARD_TIMEOUT = 10   # seconds, in the wired entry: a hung guard fails open, bounded
TOKEN_LEN = 10
# Longer than every PPROBE mark, so under redaction a rewritten command's length tells:
# `echo PPROBE-REWRITE-<token>` (33 chars) vs `echo REWRITTEN-BY-PROBE-<token>` (36).
REWRITTEN_MARK = "REWRITTEN-BY-PROBE"


@dataclass(frozen=True)
class Case:
    name: str       # the marker word and the decode line's case field
    cap: str        # the capability flag this case settles
    answer: str     # the decode line's answer word
    prompt: str     # a `{token}` slot


CASES: tuple[Case, ...] = (
    Case("allow", "post_event", "allow",
         "Run exactly this shell command, then stop: echo PPROBE-ALLOW-{token}"),
    Case("deny", "exit_2_blocks", "deny-exit2",
         "Run exactly this shell command, then stop: echo PPROBE-DENY-{token}"),
    Case("json", "json_answers", "json-deny",
         "Run exactly this shell command, then stop: echo PPROBE-JSON-{token}"),
    Case("ask", "ask", "json-ask",
         "Run exactly this shell command, then stop: echo PPROBE-ASK-{token}"),
    Case("rewrite", "rewrites_input", "json-rewrite",
         "Run exactly this shell command, then stop: echo PPROBE-REWRITE-{token}"),
)

# The JSON answers a claude-like hook may print on stdout, as tried in the zcode probe
# rounds (docs/observed-zcode-session.md); a harness that honors answers would act on them.
JSON_DENY = {"permissionDecision": {"behavior": "deny", "message": "polymorph probe: json deny"}}
JSON_ASK = {"permissionDecision": {"behavior": "ask", "message": "polymorph probe: json ask"}}
JSON_REWRITE = {"updatedInput": {"command": "echo " + REWRITTEN_MARK + "-{token}"}}


def new_token() -> str:
    rng = random.SystemRandom()
    return "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(TOKEN_LEN))


def decode_path(log: Path) -> Path:
    """`<log without .jsonl>-decode.log`, the side-file name docs/discovery.md specifies."""
    return Path(str(log.with_suffix("")) + "-decode.log")


# ---- the adapter -------------------------------------------------------------

class ProbeAdapter:
    """How a harness gets the guard wired and what its answers look like."""

    name = ""
    summary = ""

    json_deny: dict = {}
    json_ask: dict = {}
    json_rewrite: dict = {}

    def guard_path(self, scratch: Path) -> Path:
        return scratch / discover.DISCOVERY_DIR / "probe-guard.py"

    def render_guard(self, scratch: Path, log: Path, token: str) -> str:
        template = (DATA_DIR / GUARD_TEMPLATE).read_text(encoding="utf-8")
        if template.count(GUARD_CONSTANTS_MARK) != 1:
            raise RuntimeError("probe guard template is missing its constants marker")
        consts = (f"TOKEN = {token!r}\n"
                  f"CASES = {tuple(c.name for c in CASES)!r}\n"
                  f"ANSWERS = {{{', '.join(f'{c.name!r}: {c.answer!r}' for c in CASES)}}}\n"
                  f"DECODE_PATH = {str(decode_path(log))!r}\n"
                  f"JSON_DENY = {self.json_deny!r}\n"
                  f"JSON_ASK = {self.json_ask!r}\n"
                  f"JSON_REWRITE = {self.json_rewrite!r}\n")
        return template.replace(GUARD_CONSTANTS_MARK, consts)

    def wire(self, scratch: Path, log: Path, token: str) -> None:
        """Write the rendered guard and merge it into the harness config as a catch-all
        PreToolUse hook (its own entry again on a repeat run; the user's hooks stay)."""
        script = self.guard_path(scratch)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(self.render_guard(scratch, log, token), encoding="utf-8")
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
        entry = {"type": "command", "command": command, "timeout": GUARD_TIMEOUT}
        # claude-like shape; the one shipped adapter is zcode. Marker: the script's own
        # name, so a repeat probe replaces its guard entry instead of duplicating it.
        doc_path = discover._resolve(".zcode/config.json", scratch)
        doc = discover._load_doc(doc_path)
        discover._merge_hook(doc, "claude-like", "PreToolUse", "*", entry, marker="probe-guard.py")
        discover._write_doc(doc_path, doc)


class ZCodeProbeAdapter(ProbeAdapter):
    """The claude-like guard: exit 2 blocks; JSON deny/ask/rewrite go on stdout for the
    harness to honor or ignore, which is exactly what the probe observes."""

    name = "zcode"
    summary = "claude-like guard: exit 2 deny, JSON deny/ask/rewrite on stdout"
    json_deny = JSON_DENY
    json_ask = JSON_ASK
    json_rewrite = JSON_REWRITE


ADAPTERS: dict[str, ProbeAdapter] = {ZCodeProbeAdapter.name: ZCodeProbeAdapter()}


# ---- interpretation ----------------------------------------------------------

@dataclass(frozen=True)
class CapResult:
    value: bool | None   # None: untested (stays false, conservatively)
    how: str


def _case_records(selected: list[tuple[int, dict]], call_id: str) -> dict[str, dict]:
    """pre_tool/post_tool records whose call id matches; first of each kind (selected is ts-sorted)."""
    records_by_kind: dict[str, dict] = {}
    for _, record in selected:
        matched_call, _message_id = discover._ids(record["raw"])
        if matched_call != call_id:
            continue
        event_name = discover._top(record)[0]
        kind = discover._event_kind(event_name)[0] if event_name and discover._EVENT_RE.match(event_name) else None
        if kind in ("pre_tool", "post_tool") and kind not in records_by_kind:
            records_by_kind[kind] = record
    return records_by_kind


def _input_command(record: dict):
    tool_input = record["raw"].get("tool_input")
    if isinstance(tool_input, dict):
        v = tool_input.get("command")
        if isinstance(v, (str, dict)):
            return v
    return None


def _case_result(case: Case, decode_entries: dict, selected: list[tuple[int, dict]]) -> CapResult:
    decode_entry = decode_entries.get(case.name)
    if decode_entry is None:
        return CapResult(None, "untested: the guard never dispatched this case (no decode line; "
                               "a wired hook that never fires may be pending trust)")
    _decoded_case, _event, _answer, call_id = decode_entry
    records = _case_records(selected, call_id)
    pre, post = records.get("pre_tool"), records.get("post_tool")
    if pre is None:
        return CapResult(None, f"untested: the guard dispatched {case.name} (call {call_id}) "
                               "but no PreToolUse payload for it was logged")
    if case.name == "allow":
        if post is not None:
            return CapResult(True, f"observed: PostToolUse fired for the allowed call ({call_id})")
        return CapResult(False, f"observed: no PostToolUse for the allowed call ({call_id})")
    if case.name == "deny":
        if post is None:
            return CapResult(True, f"observed: the denied call was blocked "
                                   f"(PreToolUse logged, no PostToolUse; {call_id})")
        return CapResult(False, f"observed: the denied call ran anyway (Pre and Post logged; {call_id})")
    if case.name == "json":
        if post is None:
            return CapResult(True, f"observed: the JSON deny blocked the call ({call_id})")
        return CapResult(False, f"observed: the JSON deny was ignored, the call ran ({call_id})")
    if case.name == "ask":
        if post is not None:
            return CapResult(False, f"observed: the JSON ask drew no prompt and the call ran ({call_id})")
        return CapResult(None, f"untested: headless, an ask that stops the call cannot be "
                               "told apart from a block (no PostToolUse)")
    # rewrite: did the command the tool received change? Under redaction the command is a
    # {type, length} descriptor, and the rewritten echo is 3 chars longer by construction.
    if post is None:
        return CapResult(None, f"untested: no PostToolUse to compare the rewrite against ({call_id})")
    command_before = _input_command(pre)
    command_after = _input_command(post)
    if isinstance(command_before, str) and isinstance(command_after, str):   # a --full log holds the text
        hon = REWRITTEN_MARK in command_after
        return CapResult(hon, f"observed: the PostToolUse command "
                              f"{'carries' if hon else 'does not carry'} the rewritten text ({call_id})")
    if isinstance(command_before, dict) and isinstance(command_after, dict):
        if command_after.get("length", 0) > command_before.get("length", 0):
            return CapResult(True, f"derived: PostToolUse command length {command_before.get('length')} -> "
                                   f"{command_after.get('length')} (the rewritten echo is longer; {call_id})")
        if command_after.get("length") == command_before.get("length"):
            return CapResult(False, f"derived: PostToolUse command length equals PreToolUse "
                                    f"({command_before.get('length')}); the rewrite did not land ({call_id})")
    return CapResult(None, f"untested: Pre/Post commands are not comparable for {call_id}")


def read_decode(path: Path) -> tuple[dict[str, tuple[str, str, str, str]], int]:
    """`case event answer call-id` lines -> {case: (...)}, plus the count of unreadable lines."""
    out: dict[str, tuple[str, str, str, str]] = {}
    bad = 0
    if not path.is_file():
        return out, bad
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 4 and all(part and len(part) <= 128 for part in parts):
            out.setdefault(parts[0], (parts[0], parts[1], parts[2], parts[3]))
        else:
            bad += 1
    return out, bad


def interpret(selected: list[tuple[int, dict]], decode_entries: dict,
              cases: tuple[Case, ...] = CASES) -> dict[str, CapResult]:
    """Capability name -> result, one per case; a case absent from `decode_entries` reads untested."""
    return {case.cap: _case_result(case, decode_entries, selected) for case in cases}


def fragment(flag_results: dict[str, CapResult], version: str) -> str:
    """A `[capabilities]` table with one evidence comment per line; untested stays false."""
    lines = ["[capabilities]",
             f"# probe {datetime.date.today().isoformat()}, harness {version} "
             "(polymorph discover probe)"]
    for name in CAPS:
        result = flag_results.get(name) or CapResult(None, "untested: the case never ran")
        value = "true" if result.value is True else "false"
        lines.append(f"{name} = {value}   # {result.how}")
    return "\n".join(lines) + "\n"


def apply_into(draft_path: Path, capability_table: str) -> None:
    """Replace only the `[capabilities]` block of an existing draft; nothing else moves.

    Refuses a draft without the block, and restores the original if the spliced file
    stops being a valid mapping."""
    original = draft_path.read_text(encoding="utf-8")
    lines = original.splitlines()
    try:
        block_start = lines.index("[capabilities]")
    except ValueError:
        raise ValueError(f"{draft_path}: no [capabilities] block; run `polymorph discover draft` first") from None
    block_end = next((k for k in range(block_start + 1, len(lines)) if lines[k].startswith("[")),
                     len(lines))
    spliced = "\n".join(lines[:block_start] + capability_table.splitlines() + lines[block_end:]) + "\n"
    draft_path.write_text(spliced, encoding="utf-8")
    validation_errors = validate_mapping(draft_path)
    if validation_errors:
        draft_path.write_text(original, encoding="utf-8")
        raise ValueError(f"{draft_path}: the spliced draft does not validate, original restored: "
                         f"{validation_errors[0]}")


# ---- the run -----------------------------------------------------------------

def warning_block_probe(adapter: ProbeAdapter, driver: Driver, scratch: Path, full: bool,
                        step_timeout: float, total_timeout: float, stdout=None) -> str:
    run_count = len(CASES)
    head = _bold(f"WARNING: THIS RUNS A REAL AI MODEL {run_count} TIMES AND SPENDS TOKENS", stdout)
    lines = [RULE, head, RULE,
             f"polymorph discover probe will run `{driver.binary}` {run_count} time(s), one probe case per run.",
             "Every run spends tokens, and a run may take several model turns.",
             "  - If the harness is billed per token (an API key), this costs real money.",
             "  - If it is on a subscription plan (for example a coding plan), it counts against",
             "    that plan's usage limits and may be billed as extra usage.",
             "polymorph cannot tell which billing applies and cannot measure the cost.",
             "If you are not willing to pay, or are unsure what your plan bills, cancel now.", "",
             "Scope (not a cost; the cost is unknown):",
             f"  probe cases: {run_count} ({', '.join(case.name for case in CASES)})",
             f"  timeouts: {step_timeout:g}s per run, {total_timeout:g}s in total, "
             f"{GUARD_TIMEOUT:g}s on the guard hook",
             f"  harness: {driver.name}  ({adapter.summary})",
             f"  scratch dir: {scratch}  (created by polymorph; the working directory of every run)",
             f"  log: {'RAW (--full)' if full else 'redacted'}", "",
             "The probe guard denies only commands carrying this run's token; every other",
             "command passes. Tool calls are still auto-approved inside the scratch directory,",
             "which is the working directory, not a sandbox. The prompts are:"]
    lines += [f"  {case.name}: {case.prompt.format(token='<token>')}" for case in CASES]
    lines.append(RULE)
    return "\n".join(lines) + "\n"


def run(args, stdin=None, stdout=None, stderr=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    if not args.harness or args.harness not in DRIVERS:
        what = f"unknown harness {args.harness!r}" if args.harness else "--harness is required"
        print(f"polymorph discover probe: {what}. Known drivers:\n{_drivers_text()}", file=stderr)
        return 2
    driver = DRIVERS[args.harness]
    adapter = ADAPTERS.get(driver.name)
    if adapter is None:
        print(f"polymorph discover probe: no probe adapter for {driver.name!r}: nobody has "
              f"designed its guard yet (for opencode that is issue #2). A driver without an "
              "adapter cannot be probed.", file=stderr)
        return 2
    if driver.blocked:
        print(f"polymorph discover probe: the {driver.name} driver is blocked: {driver.blocked}.",
              file=stderr)
        return 2
    for flag, value in (("--step-timeout", args.step_timeout), ("--total-timeout", args.total_timeout)):
        if not isfinite(value) or value <= 0:
            print(f"polymorph discover probe: {flag} must be a finite number of seconds above 0, "
                  f"got {value!r}", file=stderr)
            return 2
    if args.full and not args.keep:
        print("polymorph discover probe: --full needs --keep: the raw log must survive the run "
              "to be worth having", file=stderr)
        return 2
    env = minimal_env()
    if shutil.which(driver.binary, path=env.get("PATH", "")) is None:
        print(f"polymorph discover probe: {driver.binary!r} is not on PATH", file=stderr)
        return 2
    if not stdin.isatty() and not args.yes:
        print("polymorph discover probe: stdin is not a terminal; refusing without --yes", file=stderr)
        return 2
    with interrupt_on_signals():
        return _run_confirmed(args, driver, adapter, env, stdin, stdout, stderr)


def _run_confirmed(args, driver, adapter, env, stdin, stdout, stderr) -> int:
    state = {"scratch": None, "keep": args.keep, "said_kept": False}
    try:
        try:
            return _confirm_and_probe(args, driver, adapter, env, stdin, stdout, stderr, state)
        except KeyboardInterrupt as e:
            print("interrupted; cleaning up", file=stderr)
            return getattr(e, "code", 130)
        finally:
            _cleanup(driver, state, stderr)
    except KeyboardInterrupt as e:
        # The first signal landed in the `finally` cleanup above; only the first raises.
        print("interrupted during cleanup; cleaning up", file=stderr)
        _cleanup(driver, state, stderr)
        return getattr(e, "code", 130)


def _cleanup(driver, state, stderr) -> None:
    """Remove the driver's wiring, then the scratch directory unless kept. Safe to run twice."""
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


def _confirm_and_probe(args, driver, adapter, env, stdin, stdout, stderr, state) -> int:
    # mkdtemp makes a fresh 0700 directory with an unpredictable name, as capture does
    scratch = state["scratch"] = Path(tempfile.mkdtemp(prefix=f"polymorph-probe-{driver.name}-")).resolve()
    block = warning_block_probe(adapter, driver, scratch, args.full, args.step_timeout,
                                args.total_timeout, stdout)
    if args.yes:
        stdout.write(short_notice(driver, CASES))
    else:
        try:
            ok = confirm(stdin, stdout, block)
        except KeyboardInterrupt:
            state["keep"] = False   # nothing was run; the directory goes, so do not claim to keep it
            raise
        if not ok:
            state["keep"] = False
            print("cancelled: nothing was run and no scratch directory was left.", file=stdout)
            return 0
    return _probe(args, driver, adapter, scratch, env, stdout, stderr)


def _probe(args, driver, adapter, scratch, env, stdout, stderr) -> int:
    log = driver.log_path(scratch)
    driver.install(scratch, args.full)          # the discovery logger, as capture wires it
    token = new_token()
    adapter.wire(scratch, log, token)           # the guard, beside it
    try:
        version = harness_version(driver, env, scratch)
    except NotImplementedError:
        version = "(unknown: the driver has no version invocation)"
    decode = decode_path(log)
    deadline = time.monotonic() + args.total_timeout
    try:
        for case in CASES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(f"{case.name}: skipped", file=stdout)
                continue
            lines_before = _line_count(log)
            prompt = case.prompt.format(token=token)
            status = run_step(driver.step_argv(prompt), scratch, env,
                              min(args.step_timeout, remaining), log)
            lines_after = _line_count(log)   # run_step already waited for the log to settle
            print(f"{case.name}: {status}, {lines_after - lines_before} log lines", file=stdout)
    finally:
        driver.remove(scratch)   # logger and guard go even if a step raised or was interrupted
    records, _other, _broken = discover._load_records(log)
    selected, _hidden = discover._select(records)
    decode_entries, unreadable = read_decode(decode)
    flag_results = interpret(selected, decode_entries)
    capability_table = fragment(flag_results, version)
    print(capability_table, end="", file=stdout)
    for name in CAPS:
        print(f"{name}: {flag_results[name].how}", file=stdout)
    if unreadable:
        print(f"{unreadable} unreadable decode line(s) skipped", file=stderr)
    if not selected:
        print("no payloads reached the logger: a wired hook that never fires may be pending "
              "trust, not a broken mapping (docs/observed-zcode-session.md)", file=stderr)
    if args.into:
        try:
            apply_into(Path(args.into), capability_table)
        except (OSError, ValueError) as e:
            print(f"polymorph discover probe: {e}", file=stderr)
            return 2
        print(f"[capabilities] replaced in {args.into}; verified and every other table untouched",
              file=stdout)
    untested_flags = [name for name in CAPS if flag_results[name].value is None]
    if untested_flags:
        print(f"untested: {', '.join(untested_flags)} -- they stay false in the table", file=stderr)
        return 1
    return 0
