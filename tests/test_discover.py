import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from polymorph import discover
from polymorph.validate import validate_mapping

SRC = str(Path(__file__).resolve().parent.parent / "src")


def run_cli(args, stdin="", env=None, clean=False):
    """clean: start from PATH only, so a test about environment names cannot see the runner's."""
    e = {"PATH": os.environ.get("PATH", "")} if clean else dict(os.environ)
    e["PYTHONPATH"] = SRC
    e.update(env or {})
    return subprocess.run([sys.executable, "-m", "polymorph", *args], input=stdin,
                          capture_output=True, text=True, env=e)


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_init_merges(self):
        cfg = self.d / ".h" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text('{"model": "x"}')
        discover.init_config("h", str(cfg), "BeforeTool", "*")
        doc = json.loads(cfg.read_text())
        self.assertEqual(doc["model"], "x")
        ev = doc["hooks"]
        self.assertEqual(list(ev), ["BeforeTool"])
        self.assertIn("/.polymorph-discovery/h.jsonl",
                      ev["BeforeTool"][0]["hooks"][0]["command"])

    def test_init_cli_defaults(self):
        cfg = self.d / "c.json"
        r = run_cli(["discover", "init", "--harness", "foo", "--config-path", str(cfg)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PreToolUse", json.loads(cfg.read_text())["hooks"])

    def test_log_silent_exit0_on_bad_stdin(self):
        out = self.d / "sub" / "l.jsonl"
        for stdin in ("", "not json {", "\x00\xff", "[1,2]"):
            r = run_cli(["discover", "log", "--full", "--out", str(out)], stdin)
            self.assertEqual((r.returncode, r.stdout), (0, ""), stdin)
        lines = [json.loads(l) for l in out.read_text().splitlines()]
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[1]["raw"], "not json {")
        self.assertEqual(lines[1]["keys"], [])

    def test_log_unwritable_still_exit0(self):
        r = run_cli(["discover", "log", "--out", "/proc/nope/x.jsonl"], "{}")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_log_no_secret_env_values(self):
        out = self.d / "l.jsonl"
        run_cli(["discover", "log", "--out", str(out)], "{}",
                env={"MY_API_TOKEN": "PLACEHOLDER_NOT_A_REAL_VALUE"})
        text = out.read_text()
        self.assertNotIn("PLACEHOLDER_NOT_A_REAL_VALUE", text)
        self.assertEqual(json.loads(text)["env_keys"], [])   # env capture is opt-in

    def test_env_flag_logs_only_project_like_names(self):
        out = self.d / "e.jsonl"
        run_cli(["discover", "log", "--env-be-very-careful", "--out", str(out)], "{}",
                env={"MY_API_TOKEN": "PLACEHOLDER_NOT_A_REAL_VALUE", "FOO_PROJECT_DIR": "/home/x/p", "HOME": "/home/x"}, clean=True)
        text = out.read_text()
        self.assertEqual(json.loads(text)["env_keys"], ["FOO_PROJECT_DIR"])
        self.assertNotIn("/home/x", text)
        self.assertNotIn("PLACEHOLDER_NOT_A_REAL_VALUE", text)

    def test_log_record_fields(self):
        out = self.d / "l.jsonl"
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls", "timeout": 1}, "cwd": "/x"}
        run_cli(["discover", "log", "--full", "--out", str(out)], json.dumps(payload))
        r = json.loads(out.read_text())
        self.assertEqual(r["raw"], payload)
        self.assertEqual(r["keys"], ["cwd", "tool_input", "tool_name"])
        self.assertEqual(r["tool_input_keys"], ["command", "timeout"])
        self.assertIn("ts", r)

    def _log(self, payloads):
        f = self.d / "l.jsonl"
        for p in payloads:
            discover.log_stdin(str(f), io.StringIO(json.dumps(p)))
        return f

    def test_draft_validates_and_redacts(self):
        f = self._log([
            {"tool_name": "run_shell", "tool_input": {"command": "rm -rf /secret/dir"},
             "cwd": "/home/me/private", "session_id": "abc", "hook_event_name": "PreToolUse"},
            {"tool_name": "write_file", "tool_input": {"file_path": "/home/me/key.pem"}},
            {"tool_name": "Grep", "tool_input": {"pattern": "x"}},
            {"tool_name": "mcp__x__y", "tool_input": {}},
            {"tool_name": "Weird`name;", "tool_input": {}},
        ])
        text = discover.draft_mapping(f, "newh")
        p = self.d / "m.toml"
        p.write_text(text)
        self.assertEqual(validate_mapping(p), [])
        for leak in ("rm -rf", "/secret", "/home/me", "key.pem", "private", "abc"):
            self.assertNotIn(leak, text)
        self.assertNotIn("verified =", text)
        self.assertIn("5 observed payloads", text)
        self.assertIn('"run_shell"', text)
        tools_sec = text.split("[tools]")[1].split("[events]")[0]
        self.assertNotIn('"mcp__x__y"', tools_sec)
        self.assertIn("mcp__x__y (1 calls): mcp tool", text)
        self.assertIn('"shell.command" = "tool_input.command"', text)
        self.assertIn('pre_tool = "PreToolUse"', text)

    def test_draft_empty_log_still_valid(self):
        f = self.d / "e.jsonl"
        f.write_text("garbage\n\n")
        p = self.d / "m.toml"
        p.write_text(discover.draft_mapping(f, "x"))
        self.assertEqual(validate_mapping(p), [])

    def test_draft_cli(self):
        f = self._log([{"tool_name": "Bash", "tool_input": {"command": "ls"}}])
        r = run_cli(["discover", "draft", str(f), "--name", "zz"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("name = \"zz\"", r.stdout)



class InitShapeTest(unittest.TestCase):
    def test_default_is_claude_settings_with_absolute_log(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "s.json"
            discover.init_config("t3code", str(cfg), log_dir=Path(d) / "logs")
            hooks = json.loads(cfg.read_text())["hooks"]
            self.assertNotIn("enabled", hooks)
            cmd = hooks["PreToolUse"][0]["hooks"][0]["command"]
            self.assertIn(str(Path(d).resolve() / "logs" / "t3code.jsonl"), cmd)
            self.assertNotIn("enabled", hooks["PreToolUse"][0]["hooks"][0])

    def test_claude_like_shape_still_available(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "s.json"
            discover.init_config("z", str(cfg), shape="claude-like")
            self.assertTrue(json.loads(cfg.read_text())["hooks"]["enabled"])


class InitAddsEventsTest(unittest.TestCase):
    def test_second_call_adds_an_event(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "s.json"
            discover.init_config("t", str(cfg), "PreToolUse")
            discover.init_config("t", str(cfg), "PostToolUse")
            self.assertEqual(sorted(json.loads(cfg.read_text())["hooks"]), ["PostToolUse", "PreToolUse"])


CLAUDE_PAYLOADS = [
    {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s1", "cwd": "/w",
     "tool_input": {"command": "echo SECRETCMD"}},
    {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "s1", "cwd": "/w",
     "tool_input": {"command": "ls", "description": "d"}},
    {"hook_event_name": "PostToolUse", "tool_name": "Read", "session_id": "s2", "cwd": "/w",
     "tool_input": {"file_path": "/etc/hostsfile"}, "tool_response": {"content": "FILEBODY"}},
    {"hook_event_name": "PreToolUse", "tool_name": "Grep", "session_id": "s2", "cwd": "/w",
     "tool_input": {"pattern": "needle", "path": "/srv"}},
    {"hook_event_name": "PreToolUse", "tool_name": "Monitor", "session_id": "s2", "cwd": "/w",
     "tool_input": {"x": 1}},
]


def _mk(d, payloads, redact=True):
    f = Path(d) / "l.jsonl"
    for p in payloads:
        discover.log_stdin(str(f), io.StringIO(json.dumps(p)), redact=redact)
    return f


class TopLevelFieldsTest(unittest.TestCase):
    def test_event_tool_session_top_level(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk(d, CLAUDE_PAYLOADS[:1] + [{"x": 1}])
            a, b = [json.loads(l) for l in f.read_text().splitlines()]
            self.assertEqual((a["event"], a["tool_name"], a["session_id"]), ("PreToolUse", "Bash", "s1"))
            self.assertEqual((b["event"], b["tool_name"], b["session_id"]), (None, None, None))

    def test_draft_and_summary_use_top_level_not_raw(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk(d, CLAUDE_PAYLOADS)
            lines = [json.loads(l) for l in f.read_text().splitlines()]
            for r in lines:           # corrupt raw: top-level must win
                r["raw"] = {"cwd": "/w", "hook_event_name": "PreToolUse"}
            f.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
            self.assertIn("Bash", discover.draft_mapping(f, "h"))
            self.assertIn("Monitor", discover.summarize(f))


class DraftEvidenceTest(unittest.TestCase):
    def test_counts_paths_guesses_unclassified(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk(d, CLAUDE_PAYLOADS)
            text = discover.draft_mapping(f, "t3")
            m = Path(d) / "m.toml"
            m.write_text(text)
            self.assertEqual(validate_mapping(m), [])
            self.assertIn('"shell.command" = "tool_input.command"   # 2/2 Bash', text)
            self.assertIn('"read.file_path" = "tool_input.file_path"   # 1/1 Read', text)
            self.assertIn('"read.pattern" = "tool_input.pattern"   # 1/1 Grep', text)
            self.assertIn('"read.path" = "tool_input.path"   # 1/1 Grep', text)
            self.assertIn("# guessed from name", text)
            self.assertIn("#   Monitor (1 calls): no name heuristic matched", text)
            self.assertNotIn("verified =", text)
            for leak in ("SECRETCMD", "/etc/hostsfile", "needle", "/srv", "FILEBODY"):
                self.assertNotIn(leak, text)

    def test_infers_claude_config_only_with_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            text = discover.draft_mapping(_mk(d, CLAUDE_PAYLOADS), "t3")
            self.assertIn('config_path = ".claude/settings.json"   # inferred', text)
            self.assertIn('template = "claude-settings"   # inferred', text)
        with tempfile.TemporaryDirectory() as d:
            text = discover.draft_mapping(_mk(d, [{"tool_name": "Bash", "cwd": "/w"}]), "t3")
            self.assertIn("unknown: set to the real location", text)
            self.assertNotIn("inferred", text)


class SummaryTest(unittest.TestCase):
    def test_summary_table(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk(d, CLAUDE_PAYLOADS)
            out = discover.summarize(f)
            self.assertIn("payloads: 5", out)
            self.assertIn("sessions: 2", out)
            self.assertIn("PreToolUse", out)
            self.assertIn("command 2/2", out)
            self.assertIn("pattern 1/1", out)
            self.assertNotIn("SECRETCMD", out)
            r = run_cli(["discover", "summary", str(f)])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("per tool", r.stdout)


class RedactTest(unittest.TestCase):
    def test_default_keeps_keys_and_drops_values(self):
        with tempfile.TemporaryDirectory() as d:
            payloads = [dict(p, cwd="/home/someone/proj", transcript_path="/home/someone/.t/x.jsonl")
                        for p in CLAUDE_PAYLOADS[:3]]
            f = _mk(d, payloads)
            text = f.read_text()
            for leak in ("SECRETCMD", "/etc/hostsfile", "FILEBODY", "/home/someone"):
                self.assertNotIn(leak, text)
            r = json.loads(text.splitlines()[0])
            c = r["raw"]["tool_input"]["command"]
            self.assertEqual(c, {"type": "string", "length": len("echo SECRETCMD")})
            self.assertNotIn("sha256", text)
            self.assertEqual(r["raw"]["cwd"], {"type": "string", "length": len("/home/someone/proj")})
            self.assertEqual(r["tool_input_keys"], ["command"])
            self.assertEqual(r["tool_name"], "Bash")
            self.assertTrue(r["redacted"])

    def test_full_keeps_raw_and_init_bakes_it(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "f.jsonl"
            discover.log_stdin(str(f), io.StringIO(json.dumps(CLAUDE_PAYLOADS[0])), redact=False)
            self.assertIn("SECRETCMD", f.read_text())
            cfg = Path(d) / "s.json"
            discover.init_config("h", str(cfg), log_dir=d)
            self.assertNotIn("--full", cfg.read_text())
            cfg2 = Path(d) / "s2.json"
            r = run_cli(["discover", "init", "--harness", "h", "--config-path", str(cfg2), "--full"])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("log --full --out", cfg2.read_text())

    def test_non_json_not_leaked_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "l.jsonl"
            r = run_cli(["discover", "log", "--out", str(out)], "TOPSECRET not json")
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            self.assertNotIn("TOPSECRET", out.read_text())


class ErrorSidecarTest(unittest.TestCase):
    def test_non_json_and_non_object_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "h.jsonl"
            for stdin in ("nope {", "[1]", "{}"):
                r = run_cli(["discover", "log", "--out", str(out)], stdin)
                self.assertEqual((r.returncode, r.stdout), (0, ""))
            errs = [json.loads(l) for l in (Path(d) / "h.errors.jsonl").read_text().splitlines()]
            self.assertEqual(len(errs), 2)
            self.assertIn("not JSON", errs[0]["error"])
            self.assertNotIn("nope", json.dumps(errs))

    def test_unwritable_log_goes_to_sidecar_when_possible(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "h.jsonl").mkdir()          # a directory where the log file should be
            r = run_cli(["discover", "log", "--out", str(Path(d) / "h.jsonl")], "{}")
            self.assertEqual((r.returncode, r.stdout), (0, ""))
            err = (Path(d) / "h.errors.jsonl").read_text()
            self.assertIn("IsADirectoryError", err)

    def test_unwritable_everywhere_still_silent(self):
        r = run_cli(["discover", "log", "--out", "/proc/nope/x.jsonl"], "{}")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))


class ReloadCheckTest(unittest.TestCase):
    def _args(self, d):
        return ["--config-path", str(Path(d) / "settings.json"), "--scratch-log", str(Path(d) / "scratch.jsonl")]

    def test_start_finish_restores_bytes_seen(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "settings.json"
            orig = b'{ "model":"x",\n "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type":"command","command":"keep"}]}]}}'
            cfg.write_bytes(orig)
            r = run_cli(["discover", "reload-check", *self._args(d)])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("--finish", r.stdout)
            doc = json.loads(cfg.read_text())
            groups = doc["hooks"]["PreToolUse"]
            self.assertEqual(len(groups), 2)                       # appended beside existing
            self.assertEqual(groups[0]["hooks"][0]["command"], "keep")
            self.assertIn("scratch.jsonl", groups[1]["hooks"][0]["command"])
            run_cli(["discover", "log", "--out", str(Path(d) / "scratch.jsonl")], "{}")
            r = run_cli(["discover", "reload-check", "--finish", *self._args(d)])
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("RELOADED", r.stdout)
            self.assertEqual(cfg.read_bytes(), orig)

    def test_finish_not_seen_still_removes_hook(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "settings.json"
            run_cli(["discover", "reload-check", *self._args(d)])      # file did not exist
            self.assertTrue(cfg.exists())
            r = run_cli(["discover", "reload-check", "--finish", *self._args(d)])
            self.assertEqual(r.returncode, 1)
            self.assertIn("NOT SEEN", r.stdout)
            self.assertFalse(cfg.exists())

    def test_finish_after_foreign_edit_strips_by_marker(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "settings.json"
            cfg.write_text('{"a": 1}')
            run_cli(["discover", "reload-check", *self._args(d)])
            doc = json.loads(cfg.read_text())
            doc["b"] = 2
            cfg.write_text(json.dumps(doc))
            run_cli(["discover", "reload-check", "--finish", *self._args(d)])
            after = json.loads(cfg.read_text())
            self.assertEqual((after["a"], after["b"]), (1, 2))
            self.assertNotIn("scratch.jsonl", cfg.read_text())

    def test_claude_like_shape_detected(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "settings.json"
            discover.init_config("z", str(cfg), shape="claude-like", log_dir=d)
            before = cfg.read_bytes()
            run_cli(["discover", "reload-check", *self._args(d)])
            evs = json.loads(cfg.read_text())["hooks"]["events"]["PreToolUse"]
            self.assertEqual(len(evs), 2)
            run_cli(["discover", "reload-check", "--finish", *self._args(d)])
            self.assertEqual(cfg.read_bytes(), before)

    def test_second_start_refused(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "settings.json").write_text("{}")
            run_cli(["discover", "reload-check", *self._args(d)])
            r = run_cli(["discover", "reload-check", *self._args(d)])
            self.assertNotEqual(r.returncode, 0)
            run_cli(["discover", "reload-check", "--finish", *self._args(d)])
            self.assertEqual(Path(d, "settings.json").read_text(), "{}")


if __name__ == "__main__":
    unittest.main()


USER_HOOK = {"matcher": "Bash", "hooks": [{"type": "command", "command": "/home/USER/my-hook.sh"}]}


class InitKeepsUserHooksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.d = Path(self._tmp.name)
        self.cfg = self.d / "s.json"

    def _init(self, **kw):
        discover.init_config("h", str(self.cfg), log_dir=self.d / "logs", **kw)
        return json.loads(self.cfg.read_text())

    def test_claude_settings_keeps_existing_groups(self):
        self.cfg.write_text(json.dumps({"hooks": {"PreToolUse": [USER_HOOK], "Stop": [USER_HOOK]}}))
        doc = self._init()
        self.assertEqual(doc["hooks"]["PreToolUse"][0], USER_HOOK)
        self.assertEqual(len(doc["hooks"]["PreToolUse"]), 2)
        self.assertEqual(doc["hooks"]["Stop"], [USER_HOOK])
        self.assertIn("discover log", doc["hooks"]["PreToolUse"][1]["hooks"][0]["command"])

    def test_claude_like_keeps_groups_and_other_hooks_keys(self):
        self.cfg.write_text(json.dumps({"hooks": {"enabled": False, "timeout": 5,
                            "events": {"PreToolUse": [USER_HOOK], "Stop": [USER_HOOK]}}}))
        doc = self._init(shape="claude-like")
        self.assertEqual(doc["hooks"]["timeout"], 5)
        self.assertEqual(doc["hooks"]["events"]["Stop"], [USER_HOOK])
        self.assertEqual(doc["hooks"]["events"]["PreToolUse"][0], USER_HOOK)
        self.assertEqual(len(doc["hooks"]["events"]["PreToolUse"]), 2)
        self.assertTrue(doc["hooks"]["enabled"])  # the logger needs hooks on

    def test_repeat_init_does_not_duplicate_the_logger(self):
        for shape in ("claude-settings", "claude-like"):
            with self.subTest(shape=shape):
                self.cfg.write_text(json.dumps({"hooks": {"PreToolUse": [USER_HOOK]}}
                                    if shape == "claude-settings" else
                                    {"hooks": {"events": {"PreToolUse": [USER_HOOK]}}}))
                self._init(shape=shape)
                doc = self._init(shape=shape)
                groups = (doc["hooks"]["PreToolUse"] if shape == "claude-settings"
                          else doc["hooks"]["events"]["PreToolUse"])
                self.assertEqual(len(groups), 2)
                self.assertEqual(groups[0], USER_HOOK)

    def test_unparsable_hooks_refused_and_file_untouched(self):
        cases = [('{"hooks": []}', "claude-settings"), ('{"hooks": []}', "claude-like"),
                 ('{"hooks": {"PreToolUse": "x"}}', "claude-settings"),
                 ('{"hooks": {"PreToolUse": [1]}}', "claude-settings"),
                 ('{"hooks": {"events": 3}}', "claude-like"),
                 ('{"hooks": {"events": {"PreToolUse": {}}}}', "claude-like"),
                 ('not json', "claude-settings"), ('[]', "claude-like")]
        for text, shape in cases:
            with self.subTest(text=text, shape=shape):
                self.cfg.write_text(text)
                with self.assertRaises(ValueError):
                    self._init(shape=shape)
                self.assertEqual(self.cfg.read_text(), text)

    def test_cli_refuses_with_message(self):
        self.cfg.write_text('{"hooks": []}')
        r = run_cli(["discover", "init", "--harness", "h", "--config-path", str(self.cfg)])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing", r.stderr)
        self.assertEqual(self.cfg.read_text(), '{"hooks": []}')


OPENCODE_PAYLOADS = [
    {"hook_event_name": "execute.before", "tool_name": "shell", "session_id": "ses_1",
     "agent": "build", "input": {"command": "echo SECRETCMD", "background": False}},
    {"hook_event_name": "execute.after", "tool_name": "shell", "session_id": "ses_1",
     "agent": "build", "input": {"command": "echo SECRETCMD", "background": False},
     "output": {"output": "OUTBODY", "content": []}},
    {"hook_event_name": "session.created", "session_id": "ses_2"},
]


class DottedEventNamesTest(unittest.TestCase):
    """OpenCode names its events execute.before, execute.after and session.created."""

    def _log(self, d):
        return _mk(d, OPENCODE_PAYLOADS)

    def test_summary_shows_the_real_event_names(self):
        with tempfile.TemporaryDirectory() as d:
            out = discover.summarize(self._log(d))
        self.assertNotIn("<invalid>", out)
        for name in ("execute.before", "execute.after", "session.created"):
            self.assertIn(name, out)

    def test_draft_guesses_pre_and_post_from_the_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            text = discover.draft_mapping(self._log(d), "oc")
            m = Path(d) / "m.toml"
            m.write_text(text)
            self.assertEqual(validate_mapping(m), [])
        self.assertIn('pre_tool = "execute.before"   # guessed from name', text)
        self.assertIn('post_tool = "execute.after"   # guessed from name', text)
        self.assertIn("session.created", text.split("[events]")[1].split("[capabilities]")[0])
        self.assertNotIn("SECRETCMD", text)

    def test_event_name_charset_stays_strict(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk(d, [{"hook_event_name": "bad name;`x`", "tool_name": "shell"},
                        {"hook_event_name": "a/b", "tool_name": "shell"},
                        {"hook_event_name": "ns:ev-1.after", "tool_name": "shell"}])
            out = discover.summarize(f)
            text = discover.draft_mapping(f, "oc")
        self.assertIn("<invalid>", out)
        self.assertIn("ns:ev-1.after", out)
        self.assertNotIn("bad name", out + text)
        self.assertNotIn("a/b", out + text)

    def test_a_mapping_may_name_a_dotted_event(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "m.toml"
            m.write_text(discover.draft_mapping(self._log(d), "oc"))
            self.assertEqual(validate_mapping(m), [])
            m.write_text(m.read_text().replace('"execute.before"', '"execute before;"'))
            self.assertNotEqual(validate_mapping(m), [])


def _oc(event, ts_off, call=None, msg=None, tool="shell", **extra):
    """An OpenCode-shaped payload; ts is set by the caller through _mk_ts."""
    p = {"hook_event_name": event, "session_id": "ses_1", "agent": "build"}
    if tool:
        p["tool_name"] = tool
    if call:
        p["id"], p["messageID"] = call, msg
    p.update(extra)
    return ts_off, p


def _mk_ts(d, items):
    """Write items [(ts, payload)] in the given order with chosen timestamps."""
    f = Path(d) / "noisy.jsonl"
    lines = []
    for ts, p in items:
        discover.log_stdin(str(f), io.StringIO(json.dumps(p)))
        lines.append(json.loads(f.read_text().splitlines()[-1]))
        lines[-1]["ts"] = 1000.0 + ts
    f.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    return f


SHELL_IN = {"command": "echo SECRETCMD", "workdir": "/srv/SECRETDIR", "background": True, "timeout": 5}
RESULT = {"output": "OUTBODY", "metadata": {"exit": 0, "truncated": False, "file": "/srv/SECRETDIR/x"},
          "content": ["OUTBODY"]}


def noisy_items():
    items = [
        # the after of call c1 lands before its before (observed with OpenCode)
        _oc("execute.after", 2, "call_1", "msg_1", input=SHELL_IN, output=RESULT),
        _oc("execute.before", 3, "call_1", "msg_1", input=SHELL_IN),
        _oc("execute.before", 4, "call_2", "msg_1", tool="read", input={"path": "/srv/SECRETDIR/f"}),
        _oc("execute.after", 5, "call_2", "msg_1", tool="read", input={"path": "/srv/SECRETDIR/f"},
            output={"output": "x"}),
        _oc("execute.before", 6, "call_3", "msg_2", input={"command": "ls"}),
        _oc("execute.before", 7, tool="shell", input={"command": "ls"}),
    ]
    items += [_oc("session.created", 10 + i, tool=None) for i in range(40)]
    return items


class NoisyLogTest(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.addCleanup(self._d.cleanup)
        self.f = _mk_ts(self._d.name, noisy_items())

    def test_summary_defaults_to_tool_events(self):
        out = discover.summarize(self.f)
        self.assertIn("payloads: 6", out)
        self.assertNotIn("session.created  ", out.split("hidden")[0])
        self.assertIn("hidden: session.created 40", out)

    def test_include_and_event_select_others(self):
        out = discover.summarize(self.f, include=["session.created"])
        self.assertIn("payloads: 46", out)
        out = discover.summarize(self.f, events=["session.created"])
        self.assertIn("payloads: 40", out)

    def test_draft_ignores_session_events_by_default(self):
        text = discover.draft_mapping(self.f, "oc")
        self.assertIn("drafted from 6 observed payloads", text)
        self.assertIn("session.created (40 payloads)", text)      # still named, never mapped
        self.assertIn('pre_tool = "execute.before"', text)

    def test_pairs_by_call_and_message_id_even_out_of_order(self):
        out = discover.summarize(self.f)
        self.assertIn("2 paired (1 arrived out of order)", out)
        self.assertIn("1 before without after", out)
        self.assertIn("1 without an id", out)

    def test_sorted_by_ts_before_aggregating(self):
        lines = [json.loads(l) for l in self.f.read_text().splitlines()]
        self.f.write_text("\n".join(json.dumps(r) for r in reversed(lines)) + "\n")
        self.assertIn("2 paired (1 arrived out of order)", discover.summarize(self.f))

    def test_cli_flags(self):
        r = run_cli(["discover", "summary", str(self.f), "--include", "session.created"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("payloads: 46", r.stdout)
        r = run_cli(["discover", "draft", str(self.f), "--name", "oc", "--event", "execute.before"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("drafted from 4 observed payloads", r.stdout)


class OutputKeyPathsTest(unittest.TestCase):
    def test_nested_output_keys_kept_values_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk_ts(d, noisy_items())
            first = json.loads(f.read_text().splitlines()[0])
            out = first["raw"]["output"]
            self.assertEqual(out["metadata"]["exit"], {"type": "number", "length": 1})
            self.assertEqual(out["output"], {"type": "string", "length": 7})
            self.assertEqual(out["content"]["type"], "array")
            text = discover.draft_mapping(f, "oc")
            self.assertIn("output.metadata.exit", text)
            self.assertIn("output.output", text)
            m = Path(d) / "m.toml"
            m.write_text(text)
            self.assertEqual(validate_mapping(m), [])

    def test_depth_is_limited_and_odd_keys_dropped(self):
        deep = {"a": {"b": {"c": {"d": {"e": "x"}}}}, "/etc/passwd": 1, "ok_key": 2}
        with tempfile.TemporaryDirectory() as d:
            f = _mk(d, [{"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_response": deep}])
            text = f.read_text()
        self.assertNotIn("/etc/passwd", text)
        self.assertIn("<invalid key>", text)
        self.assertNotIn('"e"', text)


class ToolFlagsTest(unittest.TestCase):
    def test_boolean_input_keys_listed(self):
        with tempfile.TemporaryDirectory() as d:
            text = discover.draft_mapping(_mk_ts(d, noisy_items()), "oc")
        self.assertIn("boolean", text)
        self.assertRegex(text, r"shell: .*background")
        self.assertNotIn("timeout", text.split("tool_input flags")[1].split("[payload_out]")[0])

    def test_full_log_booleans_too(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk(d, [{"hook_event_name": "x.before", "tool_name": "shell",
                         "input": {"background": True, "command": "ls"}}], redact=False)
            self.assertRegex(discover.draft_mapping(f, "oc"), r"shell: .*background")


class SchemaMarkerTest(unittest.TestCase):
    def test_lines_carry_schema_1(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk(d, CLAUDE_PAYLOADS[:1])
            self.assertEqual(json.loads(f.read_text())["schema"], 1)

    def test_old_lines_without_it_still_read(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk(d, CLAUDE_PAYLOADS)
            lines = [json.loads(l) for l in f.read_text().splitlines()]
            for r in lines:
                del r["schema"]
            f.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
            out = discover.summarize(f)
            self.assertIn("payloads: 5", out)
            self.assertIn("unmarked", out)
            self.assertIn("Bash", discover.draft_mapping(f, "h"))


class NewFeatureRedactionTest(unittest.TestCase):
    LEAKS = ("SECRETCMD", "SECRETDIR", "OUTBODY", "/srv", "/etc/passwd")

    def test_no_input_text_in_log_or_draft(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk_ts(d, noisy_items())
            texts = [f.read_text(), discover.draft_mapping(f, "oc"), discover.summarize(f),
                     discover.summarize(f, include=["session.created"])]
        for t in texts:
            for leak in self.LEAKS:
                self.assertNotIn(leak, t)

    def test_ids_kept_only_when_id_shaped(self):
        with tempfile.TemporaryDirectory() as d:
            f = _mk(d, [{"hook_event_name": "x.before", "tool_name": "shell",
                         "id": "echo SECRETCMD /srv/x", "messageID": "msg_9"}])
            r = json.loads(f.read_text())
        self.assertEqual(r["raw"]["messageID"], "msg_9")
        self.assertEqual(r["raw"]["id"]["type"], "string")
