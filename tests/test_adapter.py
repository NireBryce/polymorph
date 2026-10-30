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
[tools]
shell = "run_shell"
[capabilities]
ask = false
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


class RunAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name).resolve()
        (self.repo / ".agents" / "hooks").mkdir(parents=True)
        (self.repo / ".agents" / "polymorph" / "mappings").mkdir(parents=True)
        (self.repo / ".agents" / "polymorph" / "mappings" / "fake.toml").write_text(TOML)
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


if __name__ == "__main__":
    unittest.main()
