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


def run_cli(args, stdin="", env=None):
    e = dict(os.environ, PYTHONPATH=SRC)
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
            r = run_cli(["discover", "log", "--out", str(out)], stdin)
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
        self.assertIn("MY_API_TOKEN", json.loads(text)["env_keys"])

    def test_log_record_fields(self):
        out = self.d / "l.jsonl"
        payload = {"tool_name": "Bash", "tool_input": {"command": "ls", "timeout": 1}, "cwd": "/x"}
        run_cli(["discover", "log", "--out", str(out)], json.dumps(payload))
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
        self.assertNotIn("mcp__x__y", text)
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


if __name__ == "__main__":
    unittest.main()


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
