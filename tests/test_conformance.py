import copy
import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymorph import conformance  # noqa: E402
from polymorph.validate import validate_mapping  # noqa: E402

ROOT = Path(__file__).resolve().parents[1] / "src" / "polymorph"

MAPPINGS = sorted((ROOT / "mappings").glob("*.toml"))


def load(name):
    with open(ROOT / "mappings" / f"{name}.toml", "rb") as f:
        return tomllib.load(f)


class ValidateTests(unittest.TestCase):
    def test_shipped_mappings_valid(self):
        self.assertTrue(MAPPINGS)
        for p in MAPPINGS:
            with self.subTest(p.name):
                self.assertEqual(validate_mapping(p), [])

    def test_verified_policy(self):
        self.assertIn("verified", load("zcode"))
        self.assertNotIn("verified", load("gemini"))
        self.assertFalse(load("zcode")["capabilities"]["ask"])

    def test_wrong_tool_class_caught(self):
        m = load("claude")
        m["tools"]["shel"] = m["tools"].pop("shell")
        errs = validate_mapping(m)
        self.assertTrue(any("shel" in e for e in errs), errs)

    def test_bad_tool_name_caught(self):
        m = load("claude")
        m["tools"]["shell"] = "Bash; rm -rf /"
        self.assertTrue(validate_mapping(m))
        m["tools"]["shell"] = 7
        self.assertTrue(validate_mapping(m))

    def test_rejections(self):
        base = load("zcode")
        def bad(mut):
            m = copy.deepcopy(base)
            mut(m)
            self.assertTrue(validate_mapping(m))
        bad(lambda m: m.update(schema=2))
        bad(lambda m: m.update(surprise=1))
        bad(lambda m: m.pop("events"))
        bad(lambda m: m["capabilities"].update(ask="no"))
        bad(lambda m: m["payload_in"].update(extractor="rm_rf"))
        bad(lambda m: m["payload_in"].update(cwd="$(whoami)"))
        bad(lambda m: m["payload_out"].update(deny={"exit": 2, "stderr": "`id`"}))
        bad(lambda m: m["events"].update(pre_tool="Pre Tool"))

    def test_known_extractor_ok(self):
        m = load("zcode")
        m["delivery"] = "adapter"  # an extractor is a translation, so not direct
        m["payload_in"]["extractor"] = "apply_patch_paths"
        self.assertEqual(validate_mapping(m), [])


class FixtureShapeTests(unittest.TestCase):
    def test_fixtures_present_and_well_formed(self):
        for h in ("claude", "zcode"):
            names = {f.stem for f in conformance.fixture_files(h)}
            want = {"deny", "ask", "warn", "remind"}
            if h == "claude":
                want |= {"read-path", "read-pattern", "read-glob"}
            self.assertEqual(names, want, h)
        for f in conformance.fixture_files():
            with self.subTest(f=str(f.relative_to(ROOT))):
                self.assertEqual(conformance.check_shape(json.loads(f.read_text())), [])

    def test_zcode_native_has_camel_case_signal(self):
        for f in conformance.fixture_files("zcode"):
            n = json.loads(f.read_text())["native_in"]
            self.assertIn("hookEventName", n)
            self.assertIn("transcriptPath", n)


class RunFixturesTests(unittest.TestCase):
    def test_returns_tuple_without_crashing(self):
        passed, failures = conformance.run_fixtures()
        self.assertIsInstance(passed, int)
        self.assertIsInstance(failures, list)

    def test_adapter_conformance_when_present(self):
        try:
            import polymorph.adapter  # noqa: F401
        except ImportError:
            self.skipTest("adapter not present yet")
        passed, failures = conformance.run_fixtures()
        self.assertEqual(failures, [])


class SyntheticFixtureTests(unittest.TestCase):
    """stderr comparison and null-answer verification, pinned with a synthetic fixture."""

    MAPPING = '''schema = 1
name = "synth"
config_path = ".synth/config.json"
format = "json"
template = "claude-settings"
[events]
pre_tool = "PreToolUse"
[tools]
shell = "Bash"
[capabilities]
ask = true
post_event = true
exit_2_blocks = true
rewrites_input = false
json_answers = true
[payload_in]
tool_name = "tool_name"
"shell.command" = "tool_input.command"
[payload_out]
deny = { exit = 2, stderr = "{reason}" }
'''

    def write_root(self, fixture: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "mappings").mkdir()
        (root / "mappings" / "synth.toml").write_text(self.MAPPING)
        (root / "fixtures" / "synth").mkdir(parents=True)
        (root / "fixtures" / "synth" / "case.json").write_text(json.dumps(fixture))
        return root

    @staticmethod
    def deny_fixture(stderr_declared: bool) -> dict:
        out = {"exit": 2, "stdout": ""}
        if stderr_declared:
            out["stderr"] = "would discard changes"
        return {
            "native_in": {"tool_name": "Bash", "tool_input": {"command": "git reset --hard"}},
            "canonical_in": {"tool_name": "Bash", "tool_input": {"command": "git reset --hard"}},
            "hook_answer": {"hookSpecificOutput": {"permissionDecision": "deny",
                                                   "permissionDecisionReason": "would discard changes"}},
            "native_out": out,
        }

    def test_stderr_is_verified(self):
        passed, failures = conformance.run_fixtures(harness="synth", root=self.write_root(self.deny_fixture(True)))
        self.assertEqual((passed, failures), (1, []))
        bad = self.deny_fixture(True)
        bad["native_out"]["stderr"] = "different"
        _, failures = conformance.run_fixtures(harness="synth", root=self.write_root(bad))
        self.assertTrue(any("stderr" in f for f in failures), failures)

    def test_null_answer_fixture_is_verified(self):
        fixture = self.deny_fixture(False)
        fixture["hook_answer"] = None
        fixture["native_out"] = {"exit": 0, "stdout": ""}
        passed, failures = conformance.run_fixtures(harness="synth", root=self.write_root(fixture))
        self.assertEqual((passed, failures), (1, []))
        fixture["native_out"] = {"exit": 1, "stdout": ""}
        _, failures = conformance.run_fixtures(harness="synth", root=self.write_root(fixture))
        self.assertTrue(any("exit" in f for f in failures), failures)


if __name__ == "__main__":
    unittest.main()
