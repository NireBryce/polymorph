#!/usr/bin/env python3
"""Stdlib-only lint, run by `just lint`: src/ imports only the standard
library and polymorph itself, and no build output or caches are tracked."""
import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def foreign_imports(root):
    found = []
    for path in sorted((root / 'src').rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split('.')[0]
                if top not in sys.stdlib_module_names and top != 'polymorph':
                    found.append(f'{path.relative_to(root)}: imports {name}, which is not standard library')
    return found


def tracked_build_output(root):
    try:
        out = subprocess.run(['git', 'ls-files'], cwd=root, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return []  # no git (the flake check's source copy): nothing to inspect
    dirs = {'build', 'dist', '__pycache__', '.venv'}

    def is_output(path):
        parts = path.split('/')[:-1]   # directory components only, so docs/build-notes.md is fine
        return any(part in dirs or part.endswith('.egg-info') for part in parts)

    return [f'{line}: build output or cache is tracked' for line in out.splitlines() if is_output(line)]


def main():
    problems = foreign_imports(ROOT) + tracked_build_output(ROOT)
    for p in problems:
        print(p)
    print('lint:', 'FAILED' if problems else 'ok')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
