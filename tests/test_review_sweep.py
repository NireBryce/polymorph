"""Regression tests for the whole-repo review sweep (2026-11-01, second pass).

Each test names a fault found by reading the code and reproducing it with the CLI.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from polymorph import adapter, adopt, check, discover, equip, extractors, generate
from polymorph.env import EnvError, parse_env
from polymorph.reader_claude import ReaderError, read_claude

SRC = str(Path(__file__).resolve().parent.parent / "src")
FIXTURE = Path(__file__).parent / "fixture_repo"


def cli(args, cwd, stdin=""):
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": SRC}
    return subprocess.run([sys.executable, "-m", "polymorph", *args], input=stdin, cwd=cwd,
                          capture_output=True, text=True, env=env)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false",
                           "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          capture_output=True, text=True)


class TmpTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()


class CliAdapterTests(TmpTest):
    """`python -m polymorph adapter` is the command `generate` writes into harness configs."""

    def setUp(self):
        super().setUp()
        hooks = self.repo / ".agents" / "hooks"
        hooks.mkdir(parents=True)
        ok = hooks / "ok.sh"
        ok.write_text("#!/bin/sh\ncat >/dev/null\n")
        ok.chmod(0o755)
        self.payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})

    def test_timeout_flag_is_accepted(self):
        r = cli(["adapter", "--harness", "claude", "--timeout", "5", "--", ".agents/hooks/ok.sh"],
                self.repo, self.payload)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("unrecognized", r.stderr)

    def test_generated_command_shape_runs(self):
        # exactly what generate writes when a hook has a timeout
        cmd = generate.adapter_command("claude", ".agents/hooks/ok.sh", 7)
        argv = cmd.replace('"$CLAUDE_PROJECT_DIR/', '"' + str(self.repo) + "/").split(" ", 3)[3]
        r = subprocess.run(f"{sys.executable} -m polymorph {argv}", shell=True, cwd=self.repo,
                           input=self.payload, capture_output=True, text=True,
                           env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": SRC})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_usage_error_is_not_checked_never_exit_2(self):
        for args in (["adapter", "--harness", "claude", "--bogus", "--", "x"],
                     ["adapter", "--", ".agents/hooks/ok.sh"]):
            r = cli(args, self.repo, self.payload)
            self.assertNotEqual(r.returncode, 2, (args, r.stderr))
            self.assertIn("NOT checked", r.stderr, args)


class CheckMalformedHooksTests(TmpTest):
    def test_malformed_hook_groups_are_a_finding_not_a_crash(self):
        (self.repo / ".agents").mkdir()
        for bad in ({"PreToolUse": [5]}, {"PreToolUse": {"a": 1}}, {"PreToolUse": ["x"]}):
            (self.repo / ".agents/settings.json").write_text(json.dumps({"hooks": bad}))
            findings = check.run_check(self.repo, ["claude"])
            self.assertIn("settings-invalid", [f.code for f in findings], bad)


class ApplyPatchTests(unittest.TestCase):
    def test_a_real_patch_without_file_headers_is_visible(self):
        patch = "*** Begin Patch\n*** Update: weird\n*** End Patch"
        with self.assertRaises(extractors.ExtractorError):
            extractors.get("apply_patch_paths")({"tool_input": {"input": patch}})

    def test_text_that_only_mentions_stars_yields_no_fields(self):
        r = extractors.get("apply_patch_paths")({"tool_input": {"command": "echo '*** hi'"}})
        self.assertEqual(r, {})


class RedactionTests(unittest.TestCase):
    def test_top_level_fields_are_not_logged_raw_under_redaction(self):
        payload = {"tool": "curl -H 'Authorization: Bearer SECRET' x",
                   "event_name": "my password is hunter2",
                   "sessionId": "hunter3 /home/x/y"}
        rec = discover._record(json.dumps(payload), redact=True)
        text = json.dumps(rec)
        for leak in ("SECRET", "hunter2", "hunter3", "/home/x"):
            self.assertNotIn(leak, text)

    def test_ordinary_names_still_logged(self):
        rec = discover._record(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                           "session_id": "abc-123"}), redact=True)
        self.assertEqual((rec["event"], rec["tool_name"], rec["session_id"]),
                         ("PreToolUse", "Bash", "abc-123"))

    def test_full_still_logs_raw(self):
        rec = discover._record(json.dumps({"tool": "anything at all"}), redact=False)
        self.assertEqual(rec["tool_name"], "anything at all")


class DenyFallbackTests(unittest.TestCase):
    NO_WAY = {"capabilities": {"ask": False, "post_event": False, "exit_2_blocks": False,
                               "rewrites_input": False, "json_answers": False},
              "payload_out": {}}
    DENY = {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "no"}}

    def test_deny_that_cannot_be_expressed_is_not_checked_not_allowed(self):
        with self.assertRaises(adapter.AdapterError):
            adapter.translate_out(self.NO_WAY, self.DENY)

    def test_deny_with_exit_2_still_blocks(self):
        m = {**self.NO_WAY, "capabilities": {**self.NO_WAY["capabilities"], "exit_2_blocks": True}}
        self.assertEqual(adapter.translate_out(m, self.DENY)["exit"], 2)


class EquipTests(TmpTest):
    def setUp(self):
        super().setUp()
        shutil.copytree(FIXTURE, self.repo, dirs_exist_ok=True)

    def test_second_harness_keeps_the_first_harnesss_provenance(self):
        equip.equip(self.repo, "zcode")
        equip.equip(self.repo, "claude")
        names = {(a.harness, a.path) for a in equip.manifest_entries(self.repo)}
        self.assertIn(("zcode", ".zcode/config.json"), names)
        self.assertIn(("claude", ".claude/settings.json"), names)

    def test_invalid_env_toml_is_reported_not_swallowed(self):
        (self.repo / ".polymorph/basecamp/env.toml").write_text("schema = 1\n[harness.h]\nexports = 5\n")
        r = equip.equip(self.repo, "zcode")
        self.assertIsNone(r.env_lines)
        self.assertTrue(r.env_error)

    def test_absent_env_toml_is_not_an_error(self):
        r = equip.equip(self.repo, "zcode")
        self.assertIsNone(r.env_lines)
        self.assertFalse(r.env_error)


class EnvTests(unittest.TestCase):
    def test_exports_must_be_a_list(self):
        for bad in (5, "A=b", {"A": "b"}):
            with self.assertRaises(EnvError, msg=repr(bad)):
                parse_env({"schema": 1, "harness": {"h": {"exports": bad}}}, "env.toml")


class GenerateExistingConfigTests(TmpTest):
    def test_non_object_existing_config_is_refused_and_untouched(self):
        shutil.copytree(FIXTURE, self.repo, dirs_exist_ok=True)
        dest = self.repo / ".zcode/config.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("[]\n")
        with self.assertRaises(ValueError) as cm:
            generate.generate(self.repo, ["zcode"])
        self.assertIn("not a JSON object", str(cm.exception))
        self.assertEqual(dest.read_text(), "[]\n")


class AdoptIgnoredTests(TmpTest):
    def test_gitignored_file_is_refused_not_moved(self):
        git(self.repo, "init", "-q")
        (self.repo / ".gitignore").write_text("AGENTS.md\n")
        (self.repo / "AGENTS.md").write_text("never committed\n")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-qm", "base")
        r = adopt.adopt(self.repo)
        self.assertFalse((self.repo / "AGENTS.md").is_symlink())
        self.assertEqual([p for _, p, _ in r.refused], ["AGENTS.md"])


class DiscoverMarkerTests(TmpTest):
    def test_installing_one_logger_does_not_strip_another_harnesss(self):
        cfg = self.repo / "c.json"
        logs = self.repo / "logs"
        discover.init_config("opencode", str(cfg), "PreToolUse", "*", log_dir=logs)
        discover.init_config("code", str(cfg), "PreToolUse", "*", log_dir=logs)
        cmds = [h["command"] for g in json.loads(cfg.read_text())["hooks"]["PreToolUse"]
                for h in g["hooks"]]
        self.assertEqual(len(cmds), 2, cmds)


class ReaderTimeoutTests(TmpTest):
    def test_non_positive_or_boolean_timeouts_are_refused(self):
        (self.repo / ".agents/hooks").mkdir(parents=True)
        (self.repo / ".agents/hooks/g.sh").write_text("#!/bin/sh\n")
        for bad in (0, -5, True):
            (self.repo / ".agents/settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "timeout": bad,
                                               "command": "$CLAUDE_PROJECT_DIR/.agents/hooks/g.sh"}]}]}}))
            with self.assertRaises(ReaderError, msg=repr(bad)):
                read_claude(self.repo)


if __name__ == "__main__":
    unittest.main()
