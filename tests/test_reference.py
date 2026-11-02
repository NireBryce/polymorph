"""The generated command reference (docs/usage.md, man/polymorph.1) matches the real parser."""
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("gen_reference", ROOT / "scripts" / "gen_reference.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


class ReferenceTests(unittest.TestCase):
    def test_committed_files_match_the_parser(self):
        self.assertEqual(gen.stale(ROOT), [], "run `just reference` and commit the result")

    def test_a_stale_or_missing_file_is_found(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            self.assertEqual(sorted(gen.stale(root)), sorted(gen.build()))
            for rel, text in gen.build().items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text)
            self.assertEqual(gen.stale(root), [])
            (root / gen.MAN).write_text("old\n")
            self.assertEqual(gen.stale(root), [gen.MAN])

    def test_every_command_and_flag_is_in_both_files(self):
        from polymorph import cli

        usage, man = (ROOT / gen.USAGE_MD).read_text(), (ROOT / gen.MAN).read_text()
        for words, parser, _ in gen.commands(cli._parser()):
            self.assertIn("polymorph " + " ".join(words), usage)
            for a in gen.options(parser):
                flag = max(a.option_strings, key=len)
                self.assertIn(flag, usage, flag)
                self.assertIn(gen.esc(flag), man, flag)

    def test_every_option_has_help_text(self):
        from polymorph import cli

        for words, parser, _ in gen.commands(cli._parser()):
            for a in [*gen.positionals(parser), *gen.options(parser)]:
                self.assertTrue(a.help, f"{' '.join(words)} {a.option_strings or a.dest} has no help text")

    def test_man_page_has_the_sections(self):
        man = (ROOT / gen.MAN).read_text()
        for section in ("NAME", "SYNOPSIS", "DESCRIPTION", "COMMANDS", "FILES", "EXIT STATUS", "SEE ALSO"):
            self.assertIn(f".SH {section}\n", man)


if __name__ == "__main__":
    unittest.main()
