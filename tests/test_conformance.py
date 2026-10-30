import copy
import json
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymorph import conformance  # noqa: E402
from polymorph.validate import validate_mapping  # noqa: E402

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
            self.assertEqual(names, {"deny", "ask", "warn", "remind"}, h)
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


if __name__ == "__main__":
    unittest.main()
