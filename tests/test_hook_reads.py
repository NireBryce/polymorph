import shutil
import tomllib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymorph import check, hook_reads  # noqa: E402
from polymorph.generate import generate  # noqa: E402
from polymorph.reader_claude import ReaderError, read_claude  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixture_repo"
SCRIPT = ".agents/hooks/edit-check-posttooluse.sh"


class HookReadsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        shutil.copytree(FIXTURE, self.repo)   # its repo-local zcode mapping is an adapter mapping with no tool_response

    def declare(self, text):
        (self.repo / hook_reads.HOOK_READS_FILE).write_text(text)

    def test_declared_field_reaches_the_hook(self):
        self.declare(f'schema = 1\n[hooks."{SCRIPT}"]\nreads = ["tool_response", "cwd"]\n')
        hooks = {hook.script: hook for hook in read_claude(self.repo).hooks}
        self.assertEqual(hooks[SCRIPT].reads, ("tool_response", "cwd"))
        self.assertEqual(hooks[".agents/hooks/session-start.sh"].reads, ())

    def test_field_the_mapping_lacks_is_a_missing_field_question(self):
        self.assertEqual(generate(self.repo, ["zcode"], check=True)[0].undecided, [])
        self.declare(f'schema = 1\n[hooks."{SCRIPT}"]\nreads = ["tool_response"]\n')
        result = generate(self.repo, ["zcode"], check=True)[0]
        self.assertEqual([m.id for m in result.undecided], [f"{SCRIPT}:post_tool:missing-field"])
        self.assertIn("tool_response", result.undecided[0].detail)
        self.assertIn("hook-reads.toml", result.undecided[0].detail)

    def test_field_the_mapping_maps_raises_no_question(self):
        self.declare(f'schema = 1\n[hooks."{SCRIPT}"]\nreads = ["cwd"]\n')   # fixture mapping maps cwd
        self.assertEqual(generate(self.repo, ["zcode"], check=True)[0].undecided, [])

    def test_bad_files_are_refused(self):
        known = {SCRIPT}
        bad = {
            "unknown field": f'schema = 1\n[hooks."{SCRIPT}"]\nreads = ["tool_input.command"]\n',
            "unknown script": 'schema = 1\n[hooks.".agents/hooks/nope.sh"]\nreads = ["cwd"]\n',
            "empty reads": f'schema = 1\n[hooks."{SCRIPT}"]\nreads = []\n',
            "missing reads": f'schema = 1\n[hooks."{SCRIPT}"]\n',
            "extra key": f'schema = 1\n[hooks."{SCRIPT}"]\nreads = ["cwd"]\nnote = "x"\n',
            "wrong schema": f'schema = 2\n[hooks."{SCRIPT}"]\nreads = ["cwd"]\n',
            "extra top key": f'schema = 1\nreads = []\n[hooks."{SCRIPT}"]\nreads = ["cwd"]\n',
        }
        for label, text in bad.items():
            with self.subTest(label), self.assertRaises(hook_reads.HookReadsError):
                hook_reads.parse_hook_reads(tomllib.loads(text), known)

    def test_check_reports_an_invalid_file_as_an_error(self):
        self.declare('schema = 1\n[hooks.".agents/hooks/nope.sh"]\nreads = ["cwd"]\n')
        with self.assertRaises(ReaderError):
            read_claude(self.repo)
        findings = [f for f in check.run_check(self.repo, ["zcode"]) if f.code == "hook-reads-invalid"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "error")


if __name__ == "__main__":
    unittest.main()
