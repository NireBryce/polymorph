"""Decisions: undecided mismatches stop generation; recorded decisions apply and are reported."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from polymorph import check
from polymorph.cli import main
from polymorph.decisions import parse_decisions
from polymorph.mapping import BUILTIN_DIR

NATIVE = {"SessionStart", "BeforeTool", "AfterTool"}


def hook(matcher, script):
    return {"matcher": matcher, "hooks": [{"type": "command", "command": f'"$CLAUDE_PROJECT_DIR/.agents/hooks/{script}"'}]}


class Base(unittest.TestCase):
    """A repo whose hooks hit each mismatch kind against the built-in draft gemini mapping."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        (self.repo / ".agents/hooks").mkdir(parents=True)
        for s in ("shell-guard.sh", "mcp-guard.sh", "read-guard.sh", "post-edit.sh"):
            f = self.repo / ".agents/hooks" / s
            f.write_text("#!/bin/sh\n")
            f.chmod(0o755)
        (self.repo / ".agents/settings.json").write_text(json.dumps({"hooks": {
            "PreToolUse": [hook("Bash", "shell-guard.sh"), hook("mcp__x__y", "mcp-guard.sh"),
                           hook("Read", "read-guard.sh")],
            "PostToolUse": [hook("Edit", "post-edit.sh")],
        }}))
        (self.repo / ".agents/polymorph").mkdir()
        (self.repo / ".agents/polymorph/config.toml").write_text('enabled = ["gemini"]\n')

    def drop_post_event(self):
        """gemini ships an AfterTool event; a repo-local copy without it makes `no-event` reachable."""
        text = (BUILTIN_DIR / "gemini.toml").read_text()
        self.assertIn('post_tool = "AfterTool"\n', text)
        d = self.repo / ".agents/polymorph/mappings"
        d.mkdir()
        (d / "gemini.toml").write_text(text.replace('post_tool = "AfterTool"\n', ""))

    def decide(self, **entries):
        lines = ["schema = 1", ""]
        for did, (choice, extra) in entries.items():
            lines += [f'[decisions."{did.replace("__", ":")}"]', f'choice = "{choice}"', 'date = "2026-10-30"', *extra, ""]
        d = self.repo / ".agents/polymorph/decisions"
        d.mkdir(exist_ok=True)
        (d / "gemini.toml").write_text("\n".join(lines))

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def gen(self, *extra):
        return self.cli("generate", "--repo", str(self.repo), *extra)

    def codes(self):
        return {(f.code, f.severity) for f in check.run_check(self.repo)}

    @property
    def out_file(self):
        return self.repo / ".gemini/settings.json"


M = ".agents/hooks/mcp-guard.sh:no-tool-class"
R = ".agents/hooks/read-guard.sh:missing-field"
P = ".agents/hooks/post-edit.sh:no-event"


class UndecidedTests(Base):
    def test_undecided_exits_3_writes_nothing_and_asks(self):
        rc, out, _ = self.gen()
        self.assertEqual(rc, 3)
        self.assertFalse(self.out_file.exists())
        self.assertIn(f"question: {M}", out)
        self.assertIn(f"question: {R}", out)
        self.assertIn("options: skip | run-empty", out)
        self.assertIn("default: skip", out)
        self.assertIn("undecided item(s)", out)

    def test_no_event_question_lists_native_events(self):
        self.drop_post_event()
        rc, out, _ = self.gen()
        self.assertEqual(rc, 3)
        self.assertIn(f"question: {P}", out)
        self.assertIn("options: skip | substitute:<native event>", out)
        self.assertIn("native events: BeforeTool, SessionStart", out)

    def test_check_reports_undecided_as_error(self):
        self.assertIn(("decision-undecided", "error"), self.codes())
        self.assertEqual(self.cli("check", "--repo", str(self.repo))[0], 1)

    def test_check_flag_with_undecided_is_3_not_drift(self):
        self.assertEqual(self.gen("--check")[0], 3)

    def test_one_target_undecided_other_still_written(self):
        (self.repo / ".agents/polymorph/config.toml").write_text('enabled = ["gemini", "zcode"]\n')
        (self.repo / ".agents/settings.json").write_text(json.dumps({"hooks": {
            "PreToolUse": [hook("Bash", "shell-guard.sh"), hook("Read", "read-guard.sh")]}}))
        rc, out, _ = self.gen()
        self.assertEqual(rc, 3)
        self.assertFalse(self.out_file.exists())
        self.assertTrue((self.repo / ".zcode/config.json").exists())


class DecidedTests(Base):
    def test_decisions_apply_and_weakening_is_flagged(self):
        self.decide(**{M.replace(":", "__"): ("skip", []), R.replace(":", "__"): ("run-empty", ['note = "reads are checked elsewhere"'])})
        rc, out, _ = self.gen()
        self.assertEqual(rc, 0, out)
        self.assertIn(f"decided: {M} = skip", out)
        self.assertIn(f"weakened: {R} = run-empty", out)
        doc = json.loads(self.out_file.read_text())
        text = json.dumps(doc)
        self.assertIn("read-guard.sh", text)       # run-empty keeps the hook
        self.assertNotIn("mcp-guard.sh", text)     # skip drops it
        self.assertIn("shell-guard.sh", text)
        codes = self.codes()
        self.assertIn(("decision-weakens-guard", "warning"), codes)
        self.assertFalse({c for c, _ in codes} & {"decision-undecided", "decision-stale", "decision-invalid"})
        self.assertEqual(self.cli("check", "--repo", str(self.repo), "--strict")[0], 1)

    def test_skip_missing_field_drops_the_hook(self):
        self.decide(**{M.replace(":", "__"): ("skip", []), R.replace(":", "__"): ("skip", [])})
        self.assertEqual(self.gen()[0], 0)
        self.assertNotIn("read-guard.sh", self.out_file.read_text())
        self.assertNotIn("decision-weakens-guard", {c for c, _ in self.codes()})

    def test_no_event_skip(self):
        self.drop_post_event()
        self.decide(**{M.replace(":", "__"): ("skip", []), R.replace(":", "__"): ("skip", []), P.replace(":", "__"): ("skip", [])})
        self.assertEqual(self.gen()[0], 0)
        self.assertNotIn("post-edit.sh", self.out_file.read_text())

    def test_no_event_substitute_is_weakening_and_uses_native_event(self):
        self.drop_post_event()
        self.decide(**{M.replace(":", "__"): ("skip", []), R.replace(":", "__"): ("skip", []),
                       P.replace(":", "__"): ("substitute:BeforeTool", [])})
        rc, out, _ = self.gen()
        self.assertEqual(rc, 0, out)
        self.assertIn(f"weakened: {P} = substitute:BeforeTool", out)
        doc = json.loads(self.out_file.read_text())
        self.assertIn("post-edit.sh", json.dumps(doc["hooks"]["events"]["BeforeTool"]))
        self.assertIn(("decision-weakens-guard", "warning"), self.codes())

    def test_stale_decision_warns(self):
        self.decide(**{M.replace(":", "__"): ("skip", []), R.replace(":", "__"): ("skip", []),
                       ".agents/hooks/gone.sh__no-event": ("skip", [])})
        rc, out, _ = self.gen()
        self.assertEqual(rc, 0)
        self.assertIn("stale decision .agents/hooks/gone.sh:no-event", out)
        self.assertIn(("decision-stale", "warning"), self.codes())

    def test_decision_for_event_that_exists_is_stale(self):
        self.decide(**{M.replace(":", "__"): ("skip", []), R.replace(":", "__"): ("skip", []), P.replace(":", "__"): ("skip", [])})
        self.assertIn(("decision-stale", "warning"), self.codes())  # built-in gemini has AfterTool

    def test_extra_harness_fields_are_a_note_not_a_question(self):
        (self.repo / ".agents/settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [hook("Bash", "shell-guard.sh")]}}))
        rc, out, _ = self.gen()
        self.assertEqual(rc, 0, out)
        self.assertIn("note: gemini: payload_in maps edit.file_path", out)
        self.assertNotIn("question:", out)


class InvalidTests(Base):
    def write(self, text):
        d = self.repo / ".agents/polymorph/decisions"
        d.mkdir(exist_ok=True)
        (d / "gemini.toml").write_text(text)

    def test_invalid_file_is_error_in_generate_and_check(self):
        self.write("schema = 1\n[decisions.\"%s\"]\nchoice = \"run-empty\"\ndate = \"2026-10-30\"\n" % M)  # not allowed for no-tool-class
        rc, _, err = self.gen()
        self.assertEqual(rc, 2)
        self.assertIn("choice", err)
        self.assertFalse(self.out_file.exists())
        self.assertIn(("decision-invalid", "error"), self.codes())

    def test_parse_rejections(self):
        def errs(raw):
            return parse_decisions(raw, NATIVE)[1]
        ok = {"choice": "skip", "date": "2026-10-30"}
        self.assertEqual(errs({"schema": 1, "decisions": {P: ok}}), [])
        self.assertTrue(errs({"schema": 2, "decisions": {}}))
        self.assertTrue(errs({"schema": 1, "extra": 1}))
        self.assertTrue(errs({"schema": 1, "decisions": {"nokind": ok}}))
        self.assertTrue(errs({"schema": 1, "decisions": {"a.sh:other": ok}}))
        self.assertTrue(errs({"schema": 1, "decisions": {P: {**ok, "choice": "run-empty"}}}))
        self.assertTrue(errs({"schema": 1, "decisions": {P: {**ok, "choice": "substitute:Nope"}}}))
        self.assertTrue(errs({"schema": 1, "decisions": {P: {**ok, "choice": "substitute:"}}}))
        self.assertTrue(errs({"schema": 1, "decisions": {P: {**ok, "choice": "skip:BeforeTool"}}}))
        self.assertTrue(errs({"schema": 1, "decisions": {P: {**ok, "date": "yesterday"}}}))
        self.assertTrue(errs({"schema": 1, "decisions": {P: {**ok, "note": 3}}}))
        self.assertTrue(errs({"schema": 1, "decisions": {P: {**ok, "other": 1}}}))
        self.assertEqual(errs({"schema": 1, "decisions": {P: {**ok, "choice": "substitute:BeforeTool", "note": "x"}}}), [])

    def test_unparsable_toml(self):
        self.write("schema = = 1\n")
        self.assertIn(("decision-invalid", "error"), self.codes())
        self.assertEqual(self.gen()[0], 2)


if __name__ == "__main__":
    unittest.main()
