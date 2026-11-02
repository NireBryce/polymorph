import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from polymorph import adapter, exprs, extractors  # noqa: E402

MAPPING = {
    "schema": 1,
    "name": "fake",
    "tools": {"shell": "run_shell", "edit": ["patch_file"]},
    "capabilities": {"ask": False, "post_event": True, "exit_2_blocks": True,
                     "rewrites_input": False, "json_answers": True},
    "payload_in": {
        "tool_name": "tool",
        "shell.command": "args.cmd",
        "edit.file_path": "args.path",
        "cwd": "cwd",
    },
    "payload_out": {"deny": {"exit": 2, "stderr": "{reason}"}},
}
TOML = """
schema = 1
name = "fake"
config_path = ".fake/config.json"
format = "json"
template = "claude-settings"
[events]
pre_tool = "BeforeTool"
[tools]
shell = "run_shell"
[capabilities]
ask = false
post_event = false
exit_2_blocks = true
rewrites_input = false
json_answers = true
[payload_in]
tool_name = "tool"
"shell.command" = "args.cmd"
[payload_out]
deny = { exit = 2, stderr = "{reason}" }
"""


class ExprTests(unittest.TestCase):
    def test_lookup(self):
        self.assertEqual(exprs.lookup({"a": {"b": [1, 2]}}, "a.b.1"), 2)
        self.assertIsNone(exprs.lookup({}, "a.b", None))
        with self.assertRaises(exprs.ExprError):
            exprs.lookup({}, "a")

    def test_substitute_is_not_format(self):
        s = exprs.substitute("x {reason}", {"reason": "{0.__class__}"})
        self.assertEqual(s, "x {0.__class__}")
        with self.assertRaises(exprs.ExprError):
            exprs.substitute("{nope}", {})

    def test_render_json_safe(self):
        out = exprs.render({"m": 'a "q" {reason}'}, {"reason": 'x"\n'})
        self.assertEqual(json.loads(json.dumps(out))["m"], 'a "q" x"\n')


    def test_render_whole_placeholder_keeps_type(self):
        self.assertEqual(exprs.render("{reason}", {"reason": {"a": 1}}), {"a": 1})
        self.assertEqual(exprs.render("x {reason}", {"reason": {"a": 1}}), "x {'a': 1}")

    def test_translate_out_block_maps_to_deny(self):
        r = adapter.translate_out(MAPPING, {"decision": "block", "reason": "stop"})
        self.assertEqual((r["exit"], r["stderr"]), (2, "stop"))

    def test_translate_in_tool_response_passes_through(self):
        c = adapter.translate_in(MAPPING, {"tool": "run_shell", "args": {"cmd": "ls"},
                                           "tool_response": {"out": "x"}})
        self.assertEqual(c["tool_response"], {"out": "x"})


class ExtractorTests(unittest.TestCase):
    def test_apply_patch_paths(self):
        patch = "*** Begin Patch\n*** Update File: a/b.txt\n@@\n*** Add File: c.txt\n*** End Patch"
        r = extractors.get("apply_patch_paths")({"tool_input": {"input": patch}})
        self.assertEqual(r["tool_input.file_paths"], ["a/b.txt", "c.txt"])
        self.assertEqual(r["tool_input.file_path"], "a/b.txt")

    def test_unknown_extractor(self):
        with self.assertRaises(extractors.ExtractorError):
            extractors.get("os.system")


class PureTests(unittest.TestCase):
    def test_every_canonical_key_lands_at_its_table_path(self):
        from polymorph.canonical import CANON_PATHS
        for ckey, path in CANON_PATHS.items():
            with self.subTest(key=ckey):
                m = {"payload_in": {ckey: "native"}, "tools": {}}
                got = adapter.translate_in(m, {"native": "V"})
                node = got
                for part in path.split("."):
                    node = node[part]
                self.assertEqual(node, "V")
                self.assertNotIn("read", got)

    def test_capability_env(self):
        env = adapter.capability_env(MAPPING)
        self.assertEqual(env, {"GUARD_ASK_ENFORCED": "0", "GUARD_POST_EVENT": "1",
                               "GUARD_EXIT2_BLOCKS": "1", "GUARD_REWRITES_INPUT": "0",
                               "GUARD_JSON_ANSWERS": "1"})

    def test_translate_in(self):
        c = adapter.translate_in(MAPPING, {"tool": "run_shell", "args": {"cmd": "ls"}, "cwd": "/x"})
        self.assertEqual(c, {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/x"})

    def test_translate_in_extractor(self):
        m = {**MAPPING, "payload_in": {**MAPPING["payload_in"], "extractor": "apply_patch_paths"}}
        c = adapter.translate_in(m, {"tool": "patch_file", "tool_input": {"input": "*** Update File: z.py"}})
        self.assertEqual(c["tool_input"]["file_path"], "z.py")
        m["payload_in"]["extractor"] = "evil"
        with self.assertRaises(adapter.AdapterError):
            adapter.translate_in(m, {})

    def test_translate_out(self):
        deny = {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "no"}}
        self.assertEqual(adapter.translate_out(MAPPING, deny), {"stdout": "", "exit": 2, "stderr": "no"})
        warn = {"hookSpecificOutput": {"additionalContext": "hm"}}
        r = adapter.translate_out(MAPPING, warn)
        self.assertEqual((r["exit"], json.loads(r["stdout"])), (0, warn))
        self.assertEqual(adapter.translate_out(MAPPING, None)["exit"], 0)

    def test_translate_out_unrecognized_answer_is_never_an_allow(self):
        for ans in ({"decision": "deny", "reason": "old style"}, {}, {"weird": True}):
            with self.assertRaises(adapter.AdapterError, msg=ans):
                adapter.translate_out(MAPPING, ans)
        self.assertEqual(adapter.translate_out(MAPPING, None), {"stdout": "", "exit": 0, "stderr": ""})


class RunAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        (self.repo / ".agents" / "hooks").mkdir(parents=True)
        (self.repo / ".polymorph" / "basecamp" / "mappings").mkdir(parents=True)
        (self.repo / ".polymorph" / "basecamp" / "mappings" / "fake.toml").write_text(TOML)
        self.addCleanup(self.tmp.cleanup)

    def hook(self, name, body):
        p = self.repo / ".agents" / "hooks" / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return f".agents/hooks/{name}"

    def run_it(self, script, stdin):
        out, err = io.StringIO(), io.StringIO()
        code = adapter.run_adapter(
            ["--harness", "fake", "--repo", str(self.repo), "--", script],
            stdin=io.StringIO(stdin), stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    NATIVE = json.dumps({"tool": "run_shell", "args": {"cmd": "rm -rf /"}})

    def test_real_stub_hook_denies(self):
        script = self.hook("stub.py", f"""#!{sys.executable}
import json, os, sys
p = json.load(sys.stdin)
assert p["tool_name"] == "Bash" and p["tool_input"]["command"] == "rm -rf /", p
assert os.environ["GUARD_ASK_ENFORCED"] == "0" and os.environ["GUARD_JSON_ANSWERS"] == "1"
print(json.dumps({{"hookSpecificOutput": {{"permissionDecision": "deny",
    "permissionDecisionReason": "nope"}}}}))
""")
        code, out, err = self.run_it(script, self.NATIVE)
        self.assertEqual((code, err.strip()), (2, "nope"))

    def test_hook_exit2_becomes_deny(self):
        script = self.hook("e2.sh", "#!/bin/sh\ncat >/dev/null\necho blocked >&2\nexit 2\n")
        code, _, err = self.run_it(script, self.NATIVE)
        self.assertEqual((code, err.strip()), (2, "blocked"))

    def test_unrecognized_answer_is_not_checked_never_a_silent_allow(self):
        script = self.hook(
            "old.sh",
            '#!/bin/sh\ncat >/dev/null\necho \'{"decision": "deny", "reason": "old style"}\'\n')
        code, out, err = self.run_it(script, self.NATIVE)
        joined = out + err
        self.assertNotEqual(code, 2, joined)
        # a json_answers harness reads the answer from stdout: the NOT-checked
        # message must be there, or the fault is invisible to the harness
        self.assertIn("NOT checked", json.loads(out)["systemMessage"])
        self.assertIn("NOT checked", err)

    def test_block_answer_maps_to_deny(self):
        script = self.hook(
            "blk.sh", '#!/bin/sh\ncat >/dev/null\necho \'{"decision": "block", "reason": "stop"}\'\n')
        code, _, err = self.run_it(script, self.NATIVE)
        self.assertEqual((code, err.strip()), (2, "stop"))

    def test_hook_stdout_garbage_and_non_object_are_not_checked(self):
        for name, body in (("g.rb", 'echo "garbage"'), ("arr.sh", 'echo "[]"')):
            script = self.hook(name, f"#!/bin/sh\ncat >/dev/null\n{body}\n")
            code, out, err = self.run_it(script, self.NATIVE)
            joined = out + err
            self.assertNotEqual(code, 2, joined)
            self.assertIn("NOT checked", joined)

    def test_missing_and_nonexecutable_script_are_not_checked(self):
        (self.repo / ".agents/hooks/absent.sh").write_text("#!/bin/sh\n")
        lazy = self.repo / ".agents/hooks/lazy.sh"
        lazy.write_text("#!/bin/sh\nexit 0\n")
        lazy.chmod(0o644)
        for script in (".agents/hooks/absent.sh", ".agents/hooks/lazy.sh"):
            code, out, err = self.run_it(script, self.NATIVE)
            joined = out + err
            self.assertNotEqual(code, 2, joined)
            self.assertIn("NOT checked", joined)

    def test_missing_mapping_is_not_checked(self):
        out, err = io.StringIO(), io.StringIO()
        code = adapter.run_adapter(
            ["--harness", "nomap", "--repo", str(self.repo), "--", ".agents/hooks/lazy.sh"],
            stdin=io.StringIO(self.NATIVE), stdout=out, stderr=err)
        self.assertNotEqual(code, 2)
        self.assertIn("NOT checked", out.getvalue() + err.getvalue())

    def test_mappings_outside_trusted_dirs_is_refused(self):
        other = Path(self.tmp.name) / "other-maps"
        other.mkdir()
        (other / "fake.toml").write_text(TOML)
        out, err = io.StringIO(), io.StringIO()
        code = adapter.run_adapter(
            ["--harness", "fake", "--mappings", str(other), "--repo", str(self.repo),
             "--", ".agents/hooks/lazy.sh"],
            stdin=io.StringIO(self.NATIVE), stdout=out, stderr=err)
        self.assertNotEqual(code, 2)
        self.assertIn("NOT checked", out.getvalue() + err.getvalue())
        self.assertIn("trust", out.getvalue() + err.getvalue())

    def test_bad_timeout_is_not_checked_never_a_deny(self):
        for value in ("0", "-1", "inf", "nan"):
            out, err = io.StringIO(), io.StringIO()
            code = adapter.run_adapter(
                ["--harness", "fake", "--repo", str(self.repo), "--timeout", value,
                 "--", ".agents/hooks/lazy.sh"],
                stdin=io.StringIO(self.NATIVE), stdout=out, stderr=err)
            joined = out.getvalue() + err.getvalue()
            self.assertNotEqual(code, 2, (value, joined))
            self.assertIn("NOT checked", joined)

    def test_fault_without_json_answers_is_exit_1_stderr_visible(self):
        (self.repo / ".polymorph" / "basecamp" / "mappings" / "plain.toml").write_text(
            TOML.replace("json_answers = true", "json_answers = false"))
        script = self.hook("boom.sh", "#!/bin/sh\nexit 1\n")
        out, err = io.StringIO(), io.StringIO()
        code = adapter.run_adapter(
            ["--harness", "plain", "--repo", str(self.repo), "--", script],
            stdin=io.StringIO(self.NATIVE), stdout=out, stderr=err)
        self.assertEqual(code, 1, (out.getvalue(), err.getvalue()))
        self.assertEqual(out.getvalue(), "")
        self.assertIn("NOT checked", err.getvalue())

    def test_translation_failure_is_visible_not_deny(self):
        script = self.hook("ok.sh", "#!/bin/sh\nexit 0\n")
        code, out, err = self.run_it(script, "this is not json")
        self.assertNotEqual(code, 2)
        self.assertIn("NOT checked", err)
        self.assertIn("NOT checked", json.loads(out)["systemMessage"])
        self.assertNotIn("deny", out)

    def test_hook_crash_is_visible_not_deny(self):
        script = self.hook("crash.sh", "#!/bin/sh\nexit 1\n")
        code, _, err = self.run_it(script, self.NATIVE)
        self.assertEqual(code, 0)
        self.assertIn("NOT checked", err)

    def test_script_outside_hooks_refused(self):
        evil = self.repo / "evil.sh"
        evil.write_text("#!/bin/sh\nexit 0\n")
        evil.chmod(0o755)
        link = self.repo / ".agents" / "hooks" / "link.sh"
        os.symlink(evil, link)
        for s in ("evil.sh", ".agents/hooks/link.sh", ".agents/hooks/../../evil.sh"):
            code, _, err = self.run_it(s, self.NATIVE)
            self.assertIn("refused", err, s)
            self.assertIn("NOT checked", err, s)
            self.assertNotEqual(code, 2)


class ReviewFixTests(unittest.TestCase):
    """The 2026-11-01 review fixes: usage errors and invalid mappings land in the
    same visible NOT-checked path as every other fault, and the hook timeout is
    plumbed instead of hard-coded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        (self.repo / ".agents" / "hooks").mkdir(parents=True)
        (self.repo / ".polymorph" / "basecamp" / "mappings").mkdir(parents=True)
        (self.repo / ".polymorph" / "basecamp" / "mappings" / "fake.toml").write_text(TOML)
        slow = self.repo / ".agents" / "hooks" / "slow.sh"
        slow.write_text("#!/bin/sh\nsleep 2\n")
        slow.chmod(0o755)

    def run_adapter(self, extra, payload="{}"):
        out, err = io.StringIO(), io.StringIO()
        code = adapter.run_adapter(
            ["--harness", "fake", "--repo", str(self.repo), *extra, "--", ".agents/hooks/slow.sh"],
            stdin=io.StringIO(payload), stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    def test_usage_error_is_not_checked_never_exit_2(self):
        code, out, err = self.run_adapter(["a.sh", "b.sh"])  # two scripts: a usage error
        joined = out + err
        self.assertNotEqual(code, 2, joined)
        self.assertIn("NOT checked", joined)

    def test_invalid_mapping_is_not_checked(self):
        (self.repo / ".polymorph" / "basecamp" / "mappings" / "broken.toml").write_text(
            'schema = 1\nname = "broken"\n[capabilities]\nask = "false"\n')
        out, err = io.StringIO(), io.StringIO()
        code = adapter.run_adapter(
            ["--harness", "broken", "--repo", str(self.repo), "--", ".agents/hooks/slow.sh"],
            stdin=io.StringIO("{}"), stdout=out, stderr=err)
        joined = out.getvalue() + err.getvalue()
        self.assertNotEqual(code, 2, joined)
        self.assertIn("invalid", joined)
        self.assertIn("NOT checked", joined)

    def test_timeout_flag_kills_a_slow_hook_into_not_checked(self):
        code, out, err = self.run_adapter(["--timeout", "0.2"])
        joined = out + err
        self.assertNotEqual(code, 2, joined)
        self.assertIn("NOT checked", joined)


if __name__ == "__main__":
    unittest.main()
