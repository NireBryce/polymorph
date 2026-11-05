"""Tests for `polymorph discover probe`, against a STUB harness only.

No real harness is ever run here. The guard template is exercised with synthetic
payloads, the interpreter with a recorded-transcript fixture shaped like the observed
zcode probe rounds (docs/observed-zcode-session.md), and the whole loop with a stub
binary that simulates the claude-like semantics the probe is designed to observe.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from polymorph import capture, discover, probe, validate

ROOT = Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")
TOKEN = "tok12345abc"

# A recorded-transcript fixture: the five cases as observed on zcode 2026-11-04 --
# exit 2 blocks (no Post for the denied call), JSON deny/ask ignored (Post fires),
# the rewrite ignored (Post command length equals Pre), the allowed call gets a Post.
TRANSCRIPT_ITEMS = [
    ("allow", True), ("deny", False), ("json", True), ("ask", True), ("rewrite", True),
]


def _payload(event, call, command):
    return {"hook_event_name": event, "tool_name": "Bash", "session_id": "sz",
            "tool_use_id": call, "tool_input": {"command": command}}


def _mk_log(directory, post_lengths=None):
    """Write the transcript to a log; post_lengths adds a Post record with a command of that
    length, for cases that normally have none (a deny that ran) or a different one."""
    log_file = Path(directory) / "zcode.jsonl"
    lines = []
    calls = {name: f"t_{name}" for name, _ in TRANSCRIPT_ITEMS}
    marks = {case.name: f"PPROBE-{case.name.upper()}-{TOKEN}" for case in probe.CASES}
    for name, has_post in TRANSCRIPT_ITEMS:
        command = f"echo {marks[name]}"
        events = ("PreToolUse", "PostToolUse") if (has_post or (post_lengths and name in post_lengths)) \
            else ("PreToolUse",)
        for event in events:
            payload = _payload(event, calls[name], command)
            if event == "PostToolUse" and post_lengths and name in post_lengths:
                payload["tool_input"]["command"] = "e" * post_lengths[name]
            discover.log_stdin(str(log_file), io.StringIO(json.dumps(payload)))
            lines.append(json.loads(log_file.read_text().splitlines()[-1]))
    log_file.write_text("\n".join(json.dumps(record) for record in lines) + "\n")
    return log_file, calls


def _mk_decode(directory, calls, cases=("allow", "deny", "json", "ask", "rewrite")):
    decode_file = Path(directory) / "zcode-decode.log"
    answers = {case.name: case.answer for case in probe.CASES}
    decode_file.write_text("".join(f"{name} PreToolUse {answers[name]} {calls[name]}\n" for name in cases))
    return decode_file


def _selected_lines(log_file):
    records, _, _ = discover._load_records(log_file)
    selected, _ = discover._select(records)
    return selected


class GuardTest(unittest.TestCase):
    """The rendered guard: only token-bearing commands are acted on, ever."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.scratch = Path(self._d.name)
        self.log = self.scratch / ".polymorph/discovery/zcode.jsonl"
        self.decode = probe.decode_path(self.log)
        adapter = probe.ADAPTERS["zcode"]
        script = adapter.guard_path(self.scratch)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(adapter.render_guard(self.scratch, self.log, TOKEN), encoding="utf-8")
        self.script = str(script)

    def run_guard(self, payload):
        return subprocess.run([sys.executable, self.script], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=15)

    def decode_lines(self):
        return self.decode.read_text().splitlines() if self.decode.is_file() else []

    def test_foreign_commands_pass_untouched(self):
        for command in ("rm -rf /", "echo hello", f"echo PPROBE-DENY-wrongtok",
                        f"cat .zcode/config.json  # mentions probe-guard.py"):
            guard_result = self.run_guard(_payload("PreToolUse", "t_x", command))
            self.assertEqual(guard_result.returncode, 0, command)
            self.assertEqual(guard_result.stdout, "", command)
            self.assertEqual(self.decode_lines(), [], command)

    def test_non_payload_input_passes(self):
        for text in ("not json", "[1, 2]", "{}", '{"tool_input": "x"}',
                     '{"tool_input": {"command": 5}}'):
            guard_result = self.run_guard_raw(text)
            self.assertEqual(guard_result.returncode, 0, text)
        self.assertEqual(self.decode_lines(), [])

    def run_guard_raw(self, text):
        return subprocess.run([sys.executable, self.script], input=text,
                              capture_output=True, text=True, timeout=15)

    def test_deny_blocks_and_writes_decode(self):
        guard_result = self.run_guard(_payload("PreToolUse", "t_deny", f"echo PPROBE-DENY-{TOKEN}"))
        self.assertEqual(guard_result.returncode, 2)
        self.assertIn("probe guard", guard_result.stderr)
        self.assertEqual(self.decode_lines(), [f"deny PreToolUse deny-exit2 t_deny"])

    def test_allow_json_ask_rewrite_allow_and_decode(self):
        expected = {"allow": (0, ""), "json": (0, json.dumps(probe.JSON_DENY)),
                    "ask": (0, json.dumps(probe.JSON_ASK)),
                    "rewrite": (0, json.dumps(probe.JSON_REWRITE).replace("<token>", TOKEN))}
        for name, (exit_code, stdout_text) in expected.items():
            mark = f"PPROBE-{name.upper()}-{TOKEN}"
            guard_result = self.run_guard(_payload("PreToolUse", f"t_{name}", f"echo {mark}"))
            self.assertEqual(guard_result.returncode, exit_code, name)
            self.assertEqual(guard_result.stdout.strip(), stdout_text, name)
        decode_lines = self.decode_lines()
        self.assertEqual(len(decode_lines), 4)
        self.assertTrue(all(line.split()[0] in expected for line in decode_lines))

    def test_rendered_guard_has_no_constants_marker_left(self):
        text = Path(self.script).read_text()
        self.assertNotIn(probe.GUARD_CONSTANTS_MARK, text)
        self.assertIn(f"TOKEN = {TOKEN!r}", text)


class InterpretTest(unittest.TestCase):
    """The interpreter against the recorded-transcript fixture (the observed zcode outcomes)."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)

    def test_observed_outcomes_fill_all_five(self):
        log_file, calls = _mk_log(self._d.name)
        _mk_decode(self._d.name, calls)
        flag_results = probe.interpret(_selected_lines(log_file),
                                       probe.read_decode(probe.decode_path(log_file))[0])
        self.assertIs(flag_results["exit_2_blocks"].value, True)    # deny: Pre, no Post
        self.assertIs(flag_results["post_event"].value, True)       # allow: Pre and Post
        self.assertIs(flag_results["json_answers"].value, False)    # json deny ignored
        self.assertIs(flag_results["ask"].value, False)             # json ask drew no prompt
        self.assertIs(flag_results["rewrites_input"].value, False)  # Post command length equals Pre
        for name in validate.CAPS:
            want = "derived" if name == "rewrites_input" else "observed"
            self.assertIn(want, flag_results[name].how)
        capability_table = probe.fragment(flag_results, "zcode 3.17.2-stub")
        self.assertIn("exit_2_blocks = true", capability_table)
        self.assertIn("post_event = true", capability_table)
        self.assertIn("json_answers = false", capability_table)
        self.assertNotIn("verified", capability_table)   # never its business
        for line in capability_table.splitlines():
            if line.startswith(("ask", "post_event", "exit_2_blocks", "rewrites_input", "json_answers")):
                self.assertIn("# ", line)   # every flag carries how it was observed

    def test_missing_decode_leaves_flag_untested(self):
        log_file, calls = _mk_log(self._d.name)
        _mk_decode(self._d.name, calls, cases=("allow", "deny"))
        flag_results = probe.interpret(_selected_lines(log_file),
                                       probe.read_decode(probe.decode_path(log_file))[0])
        self.assertIs(flag_results["json_answers"].value, None)
        self.assertIn("untested", flag_results["json_answers"].how)
        capability_table = probe.fragment(flag_results, "v")
        self.assertIn("json_answers = false   # untested", capability_table)

    def test_empty_log_reads_pending_trust_hint(self):
        flag_results = probe.interpret([], {})
        self.assertTrue(all(result.value is None for result in flag_results.values()))
        self.assertIn("pending trust", flag_results["exit_2_blocks"].how)

    def test_deny_that_ran_reads_false(self):
        log_file, calls = _mk_log(self._d.name, post_lengths={"deny": 25})
        _mk_decode(self._d.name, calls)
        flag_results = probe.interpret(_selected_lines(log_file),
                                       probe.read_decode(probe.decode_path(log_file))[0])
        self.assertIs(flag_results["exit_2_blocks"].value, False)
        self.assertIn("ran anyway", flag_results["exit_2_blocks"].how)

    def test_rewrite_that_landed_reads_true(self):
        log_file, calls = _mk_log(self._d.name, post_lengths={"rewrite": 999})
        _mk_decode(self._d.name, calls)
        flag_results = probe.interpret(_selected_lines(log_file),
                                       probe.read_decode(probe.decode_path(log_file))[0])
        self.assertIs(flag_results["rewrites_input"].value, True)
        self.assertIn("derived", flag_results["rewrites_input"].how)

    def test_unreadable_decode_lines_are_counted(self):
        decode_file = Path(self._d.name) / "d.log"
        decode_file.write_text("deny PreToolUse deny-exit2 t1\ngarbage line here\nshort 2\n")
        entries, unreadable = probe.read_decode(decode_file)
        self.assertEqual(entries["deny"][3], "t1")
        self.assertEqual(unreadable, 2)


class IntoTest(unittest.TestCase):
    """--into replaces only the [capabilities] block; verified and the rest stay."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        f, _ = _mk_log(self._d.name)
        # top-level keys precede every table header in TOML, so verified goes after `name`
        draft = discover.draft_mapping(f, "zcode").replace(
            'name = "zcode"', 'name = "zcode"\nverified = "2026-11-04, live probe"', 1)
        self.draft = Path(self._d.name) / "draft.toml"
        self.draft.write_text(draft)

    def test_replaces_block_keeps_verified_and_validates(self):
        all_true = {name: probe.CapResult(True, "observed: test") for name in validate.CAPS}
        probe.apply_into(self.draft, probe.fragment(all_true, "stub 1.0"))
        text = self.draft.read_text()
        self.assertIn('verified = "2026-11-04, live probe"', text)
        self.assertIn("exit_2_blocks = true   # observed: test", text)
        self.assertNotIn("Not observable from payloads", text)
        self.assertEqual(validate.validate_mapping(self.draft), [])

    def test_refuses_a_draft_without_the_block(self):
        p = Path(self._d.name) / "other.toml"
        p.write_text('schema = 1\nname = "x"\n')
        with self.assertRaises(ValueError):
            probe.apply_into(p, "[capabilities]\n")
        self.assertEqual(p.read_text(), 'schema = 1\nname = "x"\n')


class CliGates(unittest.TestCase):
    def run_cli(self, *argv):
        from polymorph import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_no_adapter_names_the_issue(self):
        rc, _, err = self.run_cli("discover", "probe", "--harness", "opencode")
        self.assertEqual(rc, 2)
        self.assertIn("no probe adapter", err)
        self.assertIn("issue #2", err)

    def test_blocked_driver_refuses(self):
        rc, out, err = self.run_cli("discover", "probe", "--harness", "zcode", "--yes")
        self.assertEqual(rc, 2)
        self.assertIn("blocked", err)
        self.assertNotIn("WARNING", out)

    def test_unknown_harness_lists_drivers(self):
        rc, _, err = self.run_cli("discover", "probe", "--harness", "nope")
        self.assertEqual(rc, 2)
        self.assertIn("Known drivers", err)

    def test_full_requires_keep(self):
        d = capture_drivers_instance()
        with mock.patch.object(d, "blocked", ""):
            rc, _, err = self.run_cli("discover", "probe", "--harness", "zcode", "--yes", "--full")
        self.assertEqual(rc, 2)
        self.assertIn("--full needs --keep", err)


def capture_drivers_instance():
    from polymorph import capture_drivers
    return capture_drivers.DRIVERS["zcode"]


# ---- the stub end-to-end -----------------------------------------------------

ZPROBE_STUB = '''#!{python}
import json, os, shlex, subprocess, sys
ctrl = {ctrl!r}
src = {src!r}
if "--version" in sys.argv[1:]:
    print("zcode-stub 1.2.3"); sys.exit(0)
prompt = sys.argv[-1]
command = prompt.split("stop: ", 1)[1]
config = json.load(open(os.path.join(os.getcwd(), ".zcode", "config.json")))
guard_argv = None
for group in config["hooks"]["events"]["PreToolUse"]:
    for hook_entry in group["hooks"]:
        if "probe-guard.py" in hook_entry.get("command", ""):
            guard_argv = shlex.split(hook_entry["command"])
call_counter = len(open(os.path.join(ctrl, "calls.txt")).read().splitlines()) \\
    if os.path.exists(os.path.join(ctrl, "calls.txt")) else 0
open(os.path.join(ctrl, "calls.txt"), "a").write(prompt + "\\n")
payload = {{"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "sz",
            "tool_use_id": "t_%d" % call_counter, "tool_input": {{"command": command}}}}
# the guard runs as the harness would run it; zcode semantics follow: exit 2 blocks,
# JSON answers on stdout are ignored, updatedInput is ignored
guard_exit = 0
if guard_argv:
    guard_result = subprocess.run(guard_argv, input=json.dumps(payload), capture_output=True,
                                  text=True, env={{"PYTHONPATH": src}})
    guard_exit = guard_result.returncode
log_path = os.path.join(os.getcwd(), ".polymorph", "discovery", "zcode.jsonl")
def log_payload(event_payload):
    subprocess.run([sys.executable, "-m", "polymorph", "discover", "log", "--out", log_path],
                   input=json.dumps(event_payload), text=True, env={{"PYTHONPATH": src}})
log_payload(payload)
if guard_exit != 2:   # the call was not blocked: PostToolUse fires with the same command
    log_payload(dict(payload, hook_event_name="PostToolUse"))
'''


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="pm-probetest-")).resolve()
        for d in ("tmp", "work", "home", "ctrl", "bin"):
            (self.base / d).mkdir()
        stub = self.base / "bin" / "zcode"
        stub.write_text(ZPROBE_STUB.format(python=sys.executable, ctrl=str(self.base / "ctrl"), src=SRC))
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        env = {"PATH": f"{self.base / 'bin'}:/usr/bin:/bin", "HOME": str(self.base / "home")}
        for patcher in (mock.patch.dict(os.environ, env, clear=True),
                        mock.patch.object(tempfile, "tempdir", str(self.base / "tmp")),
                        mock.patch.object(capture, "SETTLE_SECONDS", 0.15)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cwd = os.getcwd()
        os.chdir(self.base / "work")
        self.addCleanup(os.chdir, self.cwd)
        self.driver = capture_drivers_instance()

    def run_probe(self, *extra):
        from polymorph import cli
        out, err = io.StringIO(), io.StringIO()
        argv = ["discover", "probe", "--harness", "zcode", "--yes", *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_stub_run_observes_all_five(self):
        with mock.patch.object(self.driver, "blocked", ""), \
                mock.patch.object(self.driver, "step_argv", lambda prompt: [str(self.base / "bin" / "zcode"), prompt]), \
                mock.patch.object(self.driver, "version_argv", lambda: [str(self.base / "bin" / "zcode"), "--version"]):
            rc, out, err = self.run_probe()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("exit_2_blocks = true", out)
        self.assertIn("post_event = true", out)
        self.assertIn("json_answers = false", out)
        self.assertIn("ask = false", out)
        self.assertIn("rewrites_input = false", out)
        for name in validate.CAPS:
            want = "derived" if name == "rewrites_input" else "observed"
            self.assertIn(f"{name}: {want}", out)
        self.assertEqual(len((self.base / "ctrl" / "calls.txt").read_text().splitlines()), 5)
        self.assertEqual(list((self.base / "tmp").iterdir()), [], "scratch left behind")

    def test_stub_run_writes_into_a_draft(self):
        seed_dir = self.base / "work" / "seed"
        seed_dir.mkdir()
        seed_log, _ = _mk_log(seed_dir)
        draft = self.base / "work" / "draft.toml"
        draft.write_text(discover.draft_mapping(seed_log, "zcode").replace(
            'name = "zcode"', 'name = "zcode"\nverified = "2026-11-04, live probe"', 1))
        with mock.patch.object(self.driver, "blocked", ""), \
                mock.patch.object(self.driver, "step_argv", lambda prompt: [str(self.base / "bin" / "zcode"), prompt]), \
                mock.patch.object(self.driver, "version_argv", lambda: [str(self.base / "bin" / "zcode"), "--version"]):
            rc, out, _ = self.run_probe("--into", str(draft))
        self.assertEqual(rc, 0, out)
        text = draft.read_text()
        self.assertIn('verified = "2026-11-04, live probe"', text)
        self.assertIn("exit_2_blocks = true", text)
        self.assertIn("# probe ", text)
        self.assertEqual(validate.validate_mapping(draft), [])

    def test_cancel_runs_nothing(self):
        stdin = io.StringIO("no\n")
        stdin.isatty = lambda: True
        with mock.patch.object(self.driver, "blocked", ""), mock.patch("sys.stdin", stdin):
            out, err = io.StringIO(), io.StringIO()
            from polymorph import cli
            argv = ["discover", "probe", "--harness", "zcode"]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = cli.main(argv)
        self.assertEqual(rc, 0)
        self.assertIn("cancelled", out.getvalue())
        self.assertFalse((self.base / "ctrl" / "calls.txt").exists())
        self.assertEqual(list((self.base / "tmp").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
