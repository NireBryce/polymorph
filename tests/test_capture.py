"""polymorph capture, against a STUB harness only. No real harness is ever run here."""
import _thread
import hashlib
import io
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from polymorph import capture, capture_drivers

ROOT = Path(__file__).resolve().parent.parent
SRC = str(ROOT / "src")

STUB = '''#!{python}
import json, os, subprocess, sys, time
ctrl = {ctrl!r}
src = {src!r}
if "--version" in sys.argv[1:]:
    print("stubcode 1.2.3"); sys.exit(0)
prompt = sys.argv[-1]
open(os.path.join(ctrl, "calls.txt"), "a").write(json.dumps(sys.argv[1:]) + "\\n")
with open("env-names.txt", "a") as f:
    f.write("\\n".join(sorted(os.environ)) + "\\n")
open("plugin-present.txt", "w").write(str(os.path.exists(".opencode/plugins/polymorph-discovery/index.js")))
mode = open(os.path.join(ctrl, "mode")).read().strip()
if mode == "hang":
    open(os.path.join(ctrl, "pid"), "w").write(str(os.getpid()))
    time.sleep(60)
if mode == "fail":
    sys.exit(3)
if mode == "late":
    # a logger child in the SAME process group (no setsid) that writes after the stub has exited
    child = ("import json, subprocess, sys, time\\n"
             "time.sleep(0.3)\\n"
             "p = {{'hook_event_name': 'execute.before', 'tool_name': 'bash', 'session_id': 's1', 'input': {{'command': 'x'}}}}\\n"
             "subprocess.run([sys.executable, '-m', 'polymorph', 'discover', 'log', '--out', sys.argv[1]],"
             " input=json.dumps(p), text=True, env={{'PYTHONPATH': sys.argv[2]}})\\n")
    subprocess.Popen([sys.executable, "-c", child, os.path.join(os.getcwd(), ".polymorph-discovery", "opencode.jsonl"), src])
    sys.exit(0)
silent = os.path.exists(os.path.join(ctrl, "silent")) and "sleep" in prompt
log = os.path.join(os.getcwd(), ".polymorph-discovery", "opencode.jsonl")
tools = [("bash", {{"command": "echo hello"}})]
if "scratch.txt" in prompt:
    tools = [("write", {{"filePath": "scratch.txt", "content": "hello"}}), ("read", {{"filePath": "scratch.txt"}})]
for name, inp in tools:
    if silent:
        break
    payload = {{"hook_event_name": "execute.before", "tool_name": name, "session_id": "s1", "input": inp}}
    subprocess.run([sys.executable, "-m", "polymorph", "discover", "log", "--out", log],
                   input=json.dumps(payload), text=True, env={{"PYTHONPATH": src}})
'''


def capture_main(argv):
    out, err = io.StringIO(), io.StringIO()
    rc = capture.main(argv, stdin=Pipe(""), stdout=out, stderr=err)
    return rc, out.getvalue(), err.getvalue()


class Tty(io.StringIO):
    def isatty(self):
        return True


class Pipe(io.StringIO):
    def isatty(self):
        return False


def tree_hash(path):
    h = hashlib.sha256()
    for p in sorted(Path(path).rglob("*")):
        h.update(str(p.relative_to(path)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


class CaptureCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="pm-captest-")).resolve()
        for d in ("tmp", "work", "home", "ctrl", "bin", "results"):
            (self.base / d).mkdir()
        (self.base / "work" / "sentinel.txt").write_text("keep me")
        (self.base / "home" / "dotfile").write_text("keep me too")
        stub = self.base / "bin" / "opencode"
        stub.write_text(STUB.format(python=sys.executable, ctrl=str(self.base / "ctrl"), src=SRC))
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        self.set_mode("ok")
        self.env = {"PATH": f"{self.base / 'bin'}:/usr/bin:/bin", "HOME": str(self.base / "home"),
                    "XDG_CONFIG_HOME": str(self.base / "home" / "cfg"),
                    "SECRET_TOKEN": "s3cret", "AWS_SECRET_ACCESS_KEY": "nope", "GITHUB_WORKSPACE": "/w"}
        for patcher in (mock.patch.dict(os.environ, self.env, clear=True),
                        mock.patch.object(tempfile, "tempdir", str(self.base / "tmp")),
                        mock.patch.object(capture, "SETTLE_SECONDS", 0.15)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cwd = os.getcwd()
        os.chdir(self.base / "work")
        self.addCleanup(os.chdir, self.cwd)

    def set_mode(self, mode):
        (self.base / "ctrl" / "mode").write_text(mode)

    def calls(self):
        p = self.base / "ctrl" / "calls.txt"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def run_cap(self, *extra, stdin=None, harness="opencode"):
        out, err = io.StringIO(), io.StringIO()
        argv = ["--harness", harness, "--out", str(self.base / "results" / "r"), *extra]
        rc = capture.main(argv, stdin=stdin if stdin is not None else Pipe(""), stdout=out, stderr=err)
        return rc, out.getvalue(), err.getvalue()

    def kept(self):
        """The scratch directories left in the (patched) system temp dir, which must be direct children."""
        found = sorted((self.base / "tmp").iterdir())
        for p in found:
            self.assertTrue(p.name.startswith("polymorph-capture-opencode-"), p.name)
        return found

    def assert_clean(self):
        self.assertEqual(list((self.base / "tmp").iterdir()), [], "scratch parent left behind")
        self.assertEqual((self.base / "work").iterdir().__next__().name, "sentinel.txt")


class Confirmation(CaptureCase):
    def test_warning_names_cost_and_step_count(self):
        rc, out, _ = self.run_cap(stdin=Tty("no\n"))
        self.assertEqual(rc, 0)
        low = out.lower()
        for word in ("per token", "subscription", "usage limits", "cannot measure", "4 times",
                     "not willing to pay", "auto-approved", "unknown"):
            self.assertIn(word, low)
        self.assertIn('[no]:', out)
        self.assertNotRegex(out, r"\$\d")

    def test_step_count_follows_steps(self):
        _, out, _ = self.run_cap("--steps", "shell", stdin=Tty("\n"))
        self.assertIn("1 TIME ", out)

    def test_anything_but_yes_cancels_and_runs_nothing(self):
        for answer in ("\n", "y\n", "n\n", "yes please\n", ""):
            with self.subTest(answer=answer):
                rc, out, _ = self.run_cap(stdin=Tty(answer))
                self.assertEqual(rc, 0)
                self.assertIn("nothing was run", out)
                self.assertIn("--print-prompts", out)
                self.assertIn("--steps", out)
                self.assertEqual(self.calls(), [])
                self.assert_clean()

    def test_cancel_with_keep_says_nothing_was_left_and_keeps_nothing(self):
        rc, out, err = self.run_cap("--keep", stdin=Tty("no\n"))
        self.assertEqual(rc, 0)
        self.assertIn("no scratch directory was left", out)
        self.assertNotIn("kept scratch directory", out + err)
        self.assert_clean()

    def test_yes_proceeds_case_insensitive(self):
        for answer in ("yes\n", "YES\n", " Yes \n"):
            with self.subTest(answer=answer):
                (self.base / "results" / "r").exists() and __import__("shutil").rmtree(self.base / "results" / "r")
                rc, out, _ = self.run_cap("--steps", "shell", stdin=Tty(answer))
                self.assertEqual(rc, 0, out)
                self.assertTrue(self.calls())

    def test_yes_flag_prints_notice_and_skips_question(self):
        rc, out, _ = self.run_cap("--yes", "--steps", "shell")
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTICE", out)
        self.assertIn("spends tokens", out)
        self.assertNotIn("Continue?", out)

    def test_non_tty_without_yes_refuses_with_2(self):
        rc, out, err = self.run_cap()
        self.assertEqual(rc, 2)
        self.assertIn("not a terminal", err)
        self.assertEqual(self.calls(), [])
        self.assert_clean()


class Running(CaptureCase):
    def test_success_results_and_cleanup(self):
        rc, out, err = self.run_cap("--yes")
        self.assertEqual(rc, 0, out + err)
        r = self.base / "results" / "r"
        self.assertEqual(sorted(p.name for p in r.iterdir()), ["draft.toml", "log.jsonl", "report.md", "summary.txt"])
        report = (r / "report.md").read_text()
        self.assertIn("polymorph version:", report)
        self.assertIn("stubcode 1.2.3", report)
        self.assertRegex(report, r"\| bash \| \d+ \|")
        self.assertIn("command", report)          # key shape
        self.assertNotIn("echo hello", report)    # values are never in the report
        self.assertIn("none", report.split("no log line")[1])
        self.assertEqual(len(self.calls()), 4 )
        for c in self.calls():
            self.assertEqual(c[:3], ["run", "--standalone", "--auto"])
        self.assertTrue((r / "draft.toml").read_text().startswith("# DRAFT"))
        self.assert_clean()

    def test_step_without_log_line_is_reported(self):
        (self.base / "ctrl" / "silent").write_text("")
        rc, _, _ = self.run_cap("--yes")
        report = (self.base / "results" / "r" / "report.md").read_text()
        self.assertIn("| background | ok | 0 |", report)
        self.assertEqual(report.split("## Steps that produced no log line")[1].split("\n")[2], "background")

    def test_steps_subset(self):
        rc, _, _ = self.run_cap("--yes", "--steps", "search", "shell")
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.calls()), 2)

    def test_unknown_step_exit_2(self):
        rc, _, err = self.run_cap("--yes", "--steps", "rm")
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls(), [])

    def test_harness_failure_cleans_up_and_reports(self):
        self.set_mode("fail")
        rc, _, _ = self.run_cap("--yes", "--steps", "shell")
        self.assertEqual(rc, 1)
        self.assertIn("exit 3", (self.base / "results" / "r" / "report.md").read_text())
        self.assert_clean()

    def test_timeout_kills_and_cleans_up(self):
        self.set_mode("hang")
        t = time.monotonic()
        rc, _, _ = self.run_cap("--yes", "--steps", "shell", "file", "--step-timeout", "0.5", "--total-timeout", "0.8")
        self.assertLess(time.monotonic() - t, 10)
        self.assertEqual(rc, 1)
        report = (self.base / "results" / "r" / "report.md").read_text()
        self.assertIn("| shell | timeout |", report)
        pid = int((self.base / "ctrl" / "pid").read_text())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assert_clean()

    def test_keyboard_interrupt_cleans_up(self):
        self.set_mode("hang")
        threading.Timer(0.6, _thread.interrupt_main).start()
        rc, _, err = self.run_cap("--yes", "--steps", "shell")
        self.assertEqual(rc, 130)
        pid = int((self.base / "ctrl" / "pid").read_text())
        time.sleep(0.2)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assert_clean()

    def test_signal_inside_the_final_cleanup_still_removes_scratch(self):
        # driver.remove runs twice on a normal run: after the steps, then in the final cleanup.
        # SIGTERM is delivered from inside the second call, before it removes anything.
        real = capture_drivers.OpenCodeDriver.remove
        calls = []

        def remove(drv, scratch):
            calls.append(scratch)
            if len(calls) == 2:
                os.kill(os.getpid(), signal.SIGTERM)
            real(drv, scratch)

        with mock.patch.object(capture_drivers.OpenCodeDriver, "remove", remove):
            rc, _, err = self.run_cap("--yes", "--steps", "shell")
        self.assertEqual(rc, 143, err)
        self.assertIn("interrupted during cleanup", err)
        self.assertGreaterEqual(len(calls), 3, "cleanup was not run again")
        self.assert_clean()
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)

    def test_logger_child_that_writes_after_the_harness_exits_is_not_lost(self):
        self.set_mode("late")
        rc, out, err = self.run_cap("--yes", "--steps", "shell")
        self.assertEqual(rc, 0, out + err)
        r = self.base / "results" / "r"
        report = (r / "report.md").read_text()
        self.assertIn("| shell | ok | 1 |", report)
        self.assertIn("none", report.split("no log line")[1])
        self.assertEqual(len((r / "log.jsonl").read_text().splitlines()), 1)

    def test_exception_in_driver_cleans_up(self):
        with mock.patch.object(capture_drivers.OpenCodeDriver, "install", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.run_cap("--yes")
        self.assert_clean()

    def test_keep_run_says_kept_and_not_that_none_was_left(self):
        rc, out, err = self.run_cap("--yes", "--steps", "shell", "--keep")
        self.assertIn("kept scratch directory", err)
        self.assertNotIn("no scratch directory was left", out + err)

    def test_scratch_is_a_fresh_directory_directly_in_the_temp_dir(self):
        self.run_cap("--yes", "--steps", "shell", "--keep")
        self.run_cap("--yes", "--steps", "shell", "--keep", "--out", str(self.base / "results" / "r2"))
        a, b = self.kept()
        self.assertNotEqual(a, b)
        self.assertEqual(a.stat().st_mode & 0o777, 0o700)

    def test_keep_leaves_scratch_without_the_logger(self):
        rc, _, err = self.run_cap("--yes", "--steps", "shell", "--keep")
        self.assertEqual(rc, 0)
        kept = self.kept()
        self.assertEqual(len(kept), 1)
        self.assertFalse((kept[0] / ".opencode" / "plugins" / "polymorph-discovery").exists())
        self.assertIn(str(kept[0]), err)

    def test_writes_only_inside_scratch(self):
        before = (tree_hash(self.base / "work"), tree_hash(self.base / "home"))
        rc, _, _ = self.run_cap("--yes", "--keep")
        self.assertEqual(rc, 0)
        self.assertEqual(before, (tree_hash(self.base / "work"), tree_hash(self.base / "home")))
        scratch = self.kept()[0]
        self.assertEqual((scratch / "plugin-present.txt").read_text(), "True")

    def test_harness_environment_is_minimal(self):
        rc, _, _ = self.run_cap("--yes", "--keep", "--steps", "shell")
        scratch = self.kept()[0]
        names = set((scratch / "env-names.txt").read_text().split())
        allowed = {"PATH", "HOME", "XDG_CONFIG_HOME", "LC_CTYPE"}   # LC_CTYPE: Python adds it itself in a C locale
        self.assertLessEqual(names, allowed)
        self.assertIn("PATH", names)
        self.assertTrue(capture.minimal_env({"A": "1", "XDG_DATA_HOME": "x", "HOME": "h", "PATH": "p"}) ==
                        {"XDG_DATA_HOME": "x", "HOME": "h", "PATH": "p"})

    def test_missing_binary_exit_2(self):
        with mock.patch.object(capture_drivers.DRIVERS["opencode"], "binary", "polymorph-no-such-harness"):
            rc, _, err = self.run_cap("--yes")
        self.assertEqual(rc, 2)
        self.assertIn("not on PATH", err)

    def test_blocked_claude_driver_refuses_before_anything_runs(self):
        rc, out, err = self.run_cap("--yes", harness="claude")
        self.assertEqual(rc, 2)
        self.assertIn("blocked", err)
        self.assertIn("--print-prompts", err)
        self.assertNotIn("WARNING", out)   # refused before the cost warning, nothing launched

    def test_unusable_out_exits_2_before_any_step(self):
        (self.base / "afile").write_text("x")
        cases = [str(self.base / "afile" / "sub")]
        if os.geteuid() != 0:
            (self.base / "ro").mkdir(mode=0o500)
            self.addCleanup((self.base / "ro").chmod, 0o700)
            cases.append(str(self.base / "ro" / "r"))
        for out in cases:
            with self.subTest(out=out):
                rc, o, err = capture_main(["--harness", "opencode", "--yes", "--out", out])
                self.assertEqual(rc, 2, o)
                self.assertIn("--out", err)
                self.assertEqual(self.calls(), [])
                self.assert_clean()

    def test_non_finite_or_non_positive_timeouts_exit_2(self):
        for flag in ("--step-timeout", "--total-timeout"):
            for bad in ("inf", "nan", "0", "-5"):
                with self.subTest(flag=flag, value=bad):
                    rc, o, err = self.run_cap("--yes", flag, bad)
                    self.assertEqual(rc, 2, o)
                    self.assertIn(flag, err)
                    self.assertEqual(self.calls(), [])
                    self.assert_clean()

    def test_existing_results_dir_not_overwritten(self):
        (self.base / "results" / "r").mkdir()
        (self.base / "results" / "r" / "x").write_text("mine")
        rc, _, err = self.run_cap("--yes")
        self.assertEqual(rc, 2)
        self.assertEqual(self.calls(), [])


SLEEPER = '''#!{python}
import os, sys, time
if "--version" in sys.argv[1:]:
    print("sleeper 0"); sys.exit(0)
open({pidfile!r}, "w").write(str(os.getpid()))
time.sleep(20)
'''


class Signals(unittest.TestCase):
    """polymorph run as a real subprocess with a stub `opencode` that sleeps; then signalled."""

    def start(self, *extra):
        self.base = Path(tempfile.mkdtemp(prefix="pm-sigtest-")).resolve()
        for d in ("tmp", "bin", "home", "work"):
            (self.base / d).mkdir()
        self.pidfile = self.base / "pid"
        stub = self.base / "bin" / "opencode"
        stub.write_text(SLEEPER.format(python=sys.executable, pidfile=str(self.pidfile)))
        stub.chmod(0o755)
        env = {"PATH": f"{self.base / 'bin'}:/usr/bin:/bin", "HOME": str(self.base / "home"),
               "TMPDIR": str(self.base / "tmp"), "PYTHONPATH": SRC}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "polymorph", "capture", "--harness", "opencode", "--yes",
             "--steps", "shell", "--out", str(self.base / "results"), *extra],
            env=env, cwd=self.base / "work", stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.proc.kill)
        deadline = time.monotonic() + 15
        while not self.pidfile.exists() or not self.pidfile.read_text():
            self.assertLess(time.monotonic(), deadline, "the stub harness never started")
            time.sleep(0.05)
        self.stub_pid = int(self.pidfile.read_text())
        self.addCleanup(self._kill_stub)

    def _kill_stub(self):
        try:
            os.kill(self.stub_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def check(self, sig, code):
        self.proc.send_signal(sig)
        self.assertEqual(self.proc.wait(timeout=15), code)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                os.kill(self.stub_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        with self.assertRaises(ProcessLookupError, msg="the harness is still running"):
            os.kill(self.stub_pid, 0)
        self.assertEqual(list((self.base / "tmp").iterdir()), [], "scratch directory left behind")

    def test_sigterm_kills_the_harness_and_removes_scratch(self):
        self.start()
        self.check(signal.SIGTERM, 143)

    def test_sighup_kills_the_harness_and_removes_scratch(self):
        self.start()
        self.check(signal.SIGHUP, 129)

    def test_sigint_exits_130(self):
        self.start()
        self.check(signal.SIGINT, 130)


class Cli(unittest.TestCase):
    def cli(self, args, stdin="", bindir=None):
        env = {"PATH": f"{bindir}:/usr/bin:/bin" if bindir else "/usr/bin:/bin", "PYTHONPATH": SRC}
        return subprocess.run([sys.executable, "-m", "polymorph", "capture", *args], input=stdin,
                              capture_output=True, text=True, env=env)

    def test_unknown_driver_lists_known_and_points_at_print_prompts(self):
        r = self.cli(["--harness", "nonesuch"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("opencode", r.stderr)
        self.assertIn("claude", r.stderr)
        self.assertIn("--print-prompts", r.stderr)

    def test_non_tty_without_yes_exits_2(self):
        r = self.cli(["--harness", "opencode"])
        self.assertEqual(r.returncode, 2)

    def test_print_prompts_runs_nothing(self):
        bindir = Path(tempfile.mkdtemp())
        ctrl = Path(tempfile.mkdtemp())
        (ctrl / "mode").write_text("ok")
        stub = bindir / "opencode"
        stub.write_text(STUB.format(python=sys.executable, ctrl=str(ctrl), src=SRC))
        stub.chmod(0o755)
        r = self.cli(["--print-prompts"], bindir=bindir)
        self.assertEqual(r.returncode, 0, r.stderr)
        for title, _ in capture.PROMPT_KIT:
            self.assertIn(title, r.stdout)
        self.assertFalse((ctrl / "calls.txt").exists())
        self.assertFalse((Path(tempfile.gettempdir()) / "polymorph-capture-never").exists())

    def test_help_marks_claude_untested(self):
        r = self.cli(["--help"])
        self.assertIn("UNTESTED", r.stdout)


class Drivers(unittest.TestCase):
    def test_plugin_code_matches_the_documented_example(self):
        doc = (ROOT / "docs/examples/opencode-discovery-plugin.js").read_text()
        tmpl = (ROOT / "src/polymorph/data/opencode-discovery-plugin.js").read_text()
        self.assertEqual(doc[doc.index("function serialize"):], tmpl[tmpl.index("function serialize"):])
        self.assertEqual(doc[doc.index("import { spawn }"):doc.index("const POLYMORPH_CMD")],
                         tmpl[tmpl.index("import { spawn }"):tmpl.index("/*@@CONSTANTS@@*/")])

    def test_rendered_plugin_has_absolute_paths(self):
        scratch = Path(tempfile.mkdtemp()).resolve()
        d = capture_drivers.DRIVERS["opencode"]
        d.install(scratch, full=False)
        js = (scratch / ".opencode/plugins/polymorph-discovery/index.js").read_text()
        self.assertIn(json.dumps(str(scratch / ".polymorph-discovery/opencode.jsonl")), js)
        self.assertIn(json.dumps(sys.executable), js)
        self.assertNotIn("@@CONSTANTS@@", js)
        self.assertNotIn("--full", js)
        d.remove(scratch)
        self.assertFalse((scratch / ".opencode/plugins/polymorph-discovery").exists())

    def test_rendered_plugin_spawns_python_directly_with_a_minimal_env(self):
        scratch = Path(tempfile.mkdtemp()).resolve()
        d = capture_drivers.DRIVERS["opencode"]
        d.install(scratch, full=True)
        js = (scratch / ".opencode/plugins/polymorph-discovery/index.js").read_text()
        d.remove(scratch)
        self.assertNotIn("/usr/bin/env", js)
        self.assertIn(f"const POLYMORPH_CMD = {json.dumps(sys.executable)}", js)
        self.assertIn(f"const POLYMORPH_ENV = {json.dumps({'PYTHONPATH': capture_drivers.PACKAGE_PARENT})}", js)
        args = json.loads(js.split("const POLYMORPH_ARGS = ")[1].split("\nconst POLYMORPH_ENV")[0])
        self.assertEqual(args[:2], ["-m", "polymorph"])
        self.assertFalse([a for a in args if a.startswith("PYTHONPATH=")])
        self.assertIn("env: POLYMORPH_ENV", js)

    def test_plugin_tool_hook_registration_cannot_throw_out_of_setup(self):
        # node is not available to the tests, so this reads the code: every ctx.tool.hook call
        # must sit inside the function handed to track(), which try/catches it
        tmpl = (ROOT / "src/polymorph/data/opencode-discovery-plugin.js").read_text()
        self.assertEqual(tmpl.count("ctx.tool.hook("), 2)
        self.assertEqual(len(re.findall(r"track\(\(\) =>\s*ctx\.tool\.hook\(", tmpl)), 2)
        track = tmpl[tmpl.index("const track"):tmpl.index("await track(")]
        self.assertIn("try {", track)
        self.assertIn("catch", track)

    def test_claude_driver_writes_project_settings_with_both_events(self):
        scratch = Path(tempfile.mkdtemp()).resolve()
        d = capture_drivers.DRIVERS["claude"]
        d.install(scratch, full=False)
        hooks = json.loads((scratch / ".claude/settings.json").read_text())["hooks"]
        self.assertEqual(sorted(hooks), ["PostToolUse", "PreToolUse"])
        self.assertIn(str(scratch / ".polymorph-discovery/claude.jsonl"), json.dumps(hooks))
        self.assertTrue(d.untested)
        self.assertEqual(d.step_argv("P")[:2], ["claude", "-p"])
        d.remove(scratch)
        self.assertFalse((scratch / ".claude").exists())

    def test_prompt_kit_in_docs_matches_the_code(self):
        docs = (ROOT / "docs/discovery.md").read_text()
        for title, text in capture.PROMPT_KIT:
            for line in text.splitlines():
                self.assertIn("> " + line, docs)


if __name__ == "__main__":
    unittest.main()
