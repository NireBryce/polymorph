import importlib.util
import pathlib
import subprocess
import tempfile
import unittest

_spec = importlib.util.spec_from_file_location('polymorph_lint', pathlib.Path(__file__).resolve().parent / 'lint.py')
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


def repo_with(*tracked):
    d = pathlib.Path(tempfile.mkdtemp())
    subprocess.run(['git', 'init', '-q'], cwd=d, check=True)
    for rel in tracked:
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('x\n')
    subprocess.run(['git', 'add', '-A'], cwd=d, check=True)
    return d


class TrackedBuildOutput(unittest.TestCase):
    def test_each_kind_of_build_output_is_reported(self):
        for rel in ('build/lib/polymorph/cli.py', 'dist/polymorph-0.0.1.tar.gz',
                    'src/polymorph.egg-info/PKG-INFO', 'src/polymorph/__pycache__/x.pyc',
                    '.venv/bin/python'):
            with self.subTest(rel):
                self.assertEqual(len(lint.tracked_build_output(repo_with(rel))), 1)

    def test_ordinary_files_are_not_reported(self):
        d = repo_with('src/polymorph/cli.py', 'docs/build-notes.md', 'src/polymorph/distance.py')
        self.assertEqual(lint.tracked_build_output(d), [])


if __name__ == '__main__':
    unittest.main()
