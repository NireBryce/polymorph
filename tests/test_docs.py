"""Each docs check, shown failing on a bad tree and passing on a good one."""
import importlib.util
import pathlib
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location('polymorph_docs_checks', pathlib.Path(__file__).with_name('docs.py'))
docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docs)

SKILL = '---\nname: demo\ndescription: Does a thing.\nwhen_to_use: Always.\n---\n\n# Demo\n'
CLI = '''
sub = p.add_subparsers(dest="cmd")
g = sub.add_parser("generate", help="x")
d = sub.add_parser("discover", help="x")
ds = d.add_subparsers(dest="dcmd")
di = ds.add_parser("init", help="x")
'''


def write(root, name, text):
    path = pathlib.Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class DocsChecks(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        write(self.root, 'AGENTS.md', '| `demo` | a demo |\n')
        write(self.root, '.agents/skills/demo/SKILL.md', SKILL)
        write(self.root, 'README.md', '> **Experimental and unvetted.**\n[a](docs/a.md)\n')
        write(self.root, 'docs/a.md', 'hi\n')
        write(self.root, 'src/polymorph/cli.py', CLI)

    def test_good_tree_passes(self):
        for check in docs.CHECKS:
            if check is not docs.check_cli:
                self.assertEqual(check(self.root), [], check.__name__)

    def test_skill_not_listed(self):
        write(self.root, 'AGENTS.md', 'nothing\n')
        self.assertTrue(any('not in the skill table' in p for p in docs.check_skills(self.root)))

    def test_skill_listed_but_missing(self):
        write(self.root, 'AGENTS.md', '| `demo` | a |\n| `ghost` | b |\n')
        self.assertTrue(any('ghost' in p for p in docs.check_skills(self.root)))

    def test_skill_bad_frontmatter(self):
        write(self.root, '.agents/skills/demo/SKILL.md', SKILL.replace('when_to_use', 'when-to-use'))
        self.assertTrue(any('unknown frontmatter key' in p for p in docs.check_skills(self.root)))
        write(self.root, '.agents/skills/demo/SKILL.md', '# no frontmatter\n')
        self.assertTrue(any('no frontmatter' in p for p in docs.check_skills(self.root)))
        write(self.root, '.agents/skills/demo/SKILL.md', SKILL.replace('name: demo', 'name: other'))
        self.assertTrue(any('directory name' in p for p in docs.check_skills(self.root)))

    def test_broken_link(self):
        write(self.root, 'docs/b.md', '[x](missing.md) and `[y](ignored.md)`\n')
        problems = docs.check_links(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn('missing.md', problems[0])

    def test_cli_subcommands_found(self):
        self.assertEqual(sorted(docs.cli_subcommands(self.root)), ['discover', 'discover init', 'generate'])

    def test_undocumented_subcommand(self):
        write(self.root, 'docs/cli.md', 'polymorph generate\npolymorph discover\n')
        problems = docs.check_cli(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn('discover init', problems[0])
        write(self.root, 'docs/cli.md', 'polymorph generate\npolymorph discover\npolymorph discover init\n')
        self.assertEqual(docs.check_cli(self.root), [])

    def test_missing_cli_md_is_a_problem(self):
        problems = docs.check_cli(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn('docs/cli.md', problems[0])
        self.assertIn('missing', problems[0])

    def test_banner_missing(self):
        write(self.root, 'README.md', '# polymorph\n')
        self.assertEqual(len(docs.check_banner(self.root)), 1)

    def test_home_path(self):
        bad = '/ho' + 'me/realperson'  # built in pieces so this file passes its own check
        write(self.root, 'docs/c.md', f'see {bad}/project and /home/USER/ok\n')
        problems = docs.check_paths(self.root)
        self.assertEqual(len(problems), 1)
        self.assertIn(bad, problems[0])


if __name__ == '__main__':
    unittest.main()
