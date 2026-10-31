#!/usr/bin/env python3
"""Mechanical checks on the docs, so keeping them true needs less memory.

    python3 tests/docs.py        # from anywhere; `just docs`

Each check takes the repository root and returns a list of problems.
tests/test_docs.py runs every check on a bad tree to show it can fail.

- skills:  every .agents/skills/<name>/ has a SKILL.md with frontmatter
           (name = directory, description, optional when_to_use, no other
           key) and a row in AGENTS.md's skill table; every row has a skill.
- links:   every relative Markdown link points at a file that exists.
- cli:     every subcommand in src/polymorph/cli.py, or registered by a function
           cli.py imports and hands its subparsers to, appears in docs/cli.md
           (a missing docs/cli.md is a problem).
- banner:  README.md carries the "Experimental and unvetted" notice.
- cost:    README.md has the "Cost and responsibility" heading and docs/cli.md and
           docs/discovery.md link to it.
- paths:   no text file holds a real home directory path.
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {'.git', '.venv', '__pycache__', 'result', '.polymorph-discovery'}
BANNER = 'Experimental and unvetted'
PLACEHOLDER_USERS = {'USER', 'user', 'you', 'name', 'NAME', 'me', 'example', 'username', 'runner', 'x', 'someone'}


def files(root):
    """Tracked files when git can say, else every file outside caches."""
    try:
        out = subprocess.run(['git', 'ls-files'], cwd=root, capture_output=True, text=True, check=True).stdout
        paths = [root / line for line in out.splitlines()]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for p in root.rglob('*') if p.is_file() and not SKIP_DIRS & set(p.relative_to(root).parts)]
    return [p for p in paths if p.is_file() and not p.is_symlink()]


def text_of(path):
    try:
        return path.read_text()
    except (UnicodeDecodeError, OSError):
        return None


def rel(root, path):
    return path.relative_to(root).as_posix()


def frontmatter(text):
    if not text.startswith('---\n') or '\n---\n' not in text[4:] + '\n':
        return None
    block = text[4:].split('\n---', 1)[0]
    fields = {}
    for line in block.splitlines():
        if line.strip():
            key, _, value = line.partition(': ')
            fields[key.strip()] = value.strip()
    return fields


def check_skills(root):
    problems = []
    agents = root / 'AGENTS.md'
    listed = set(re.findall(r'^\| `([a-z0-9-]+)` \|', agents.read_text(), re.M)) if agents.exists() else set()
    if not agents.exists():
        problems.append('AGENTS.md: missing')
    skills = root / '.agents' / 'skills'
    dirs = sorted(p for p in skills.iterdir() if p.is_dir()) if skills.is_dir() else []
    for d in dirs:
        where = f'.agents/skills/{d.name}'
        md = d / 'SKILL.md'
        if not md.exists():
            problems.append(f'{where}: no SKILL.md')
            continue
        fields = frontmatter(md.read_text())
        if fields is None:
            problems.append(f'{where}/SKILL.md: no frontmatter')
            continue
        if fields.get('name') != d.name:
            problems.append(f'{where}/SKILL.md: name {fields.get("name")!r} is not the directory name')
        if not fields.get('description'):
            problems.append(f'{where}/SKILL.md: no description')
        for key in sorted(set(fields) - {'name', 'description', 'when_to_use'}):
            problems.append(f'{where}/SKILL.md: unknown frontmatter key {key!r}')
        if d.name not in listed:
            problems.append(f'AGENTS.md: skill `{d.name}` is not in the skill table')
    for name in sorted(listed - {d.name for d in dirs}):
        problems.append(f'AGENTS.md: lists skill `{name}`, which has no .agents/skills/{name}/')
    return problems


def check_links(root):
    problems = []
    for path in files(root):
        if path.suffix != '.md':
            continue
        text = re.sub(r'```.*?```', '', path.read_text(), flags=re.S)
        text = re.sub(r'`[^`\n]*`', '', text)
        for target in re.findall(r'\]\(([^)\s]+)\)', text):
            if re.match(r'[a-z][a-z0-9+.-]*:', target, re.I) or target.startswith('#'):
                continue
            dest = (path.parent / target.split('#')[0]).resolve()
            if not dest.exists():
                problems.append(f'{rel(root, path)}: link to {target} does not resolve')
    return problems


def cli_subcommands(root):
    """Names like 'generate' and 'discover init' from the add_parser calls."""
    text = (root / 'src' / 'polymorph' / 'cli.py').read_text()
    owner = {m.group(1): m.group(2) for m in re.finditer(r'(\w+)\s*=\s*(\w+)\.add_subparsers', text)}  # subparsers var -> parser var
    names, parent_name = {}, {}
    for m in re.finditer(r'(\w+)\s*=\s*(\w+)\.add_parser\(\s*["\']([\w-]+)["\']', text):
        var, subs, name = m.groups()
        parent = owner.get(subs)
        names[var] = name
        parent_name[var] = names.get(parent) if parent in names else None
    found = [f'{parent_name[v]} {n}' if parent_name[v] else n for v, n in names.items()]
    # A subcommand registered in another module: cli.py imports a function
    # (`from .capture import build_parser as _capture_parser`) and hands it a
    # subparsers object (`_capture_parser(sub)`); that function's first
    # parameter is what it calls add_parser on.
    imported = {m.group(3) or m.group(2): (m.group(1), m.group(2))
                for m in re.finditer(r'from \.(\w+) import (\w+)(?: as (\w+))?', text)}
    for m in re.finditer(r'\b(\w+)\(\s*(\w+)\s*\)', text):
        alias, subs = m.groups()
        if alias not in imported or subs not in owner:
            continue
        module, func = imported[alias]
        path = root / 'src' / 'polymorph' / f'{module}.py'
        d = re.search(rf'def {func}\(\s*(\w+)', path.read_text()) if path.exists() else None
        if not d:
            continue
        parent = names.get(owner[subs])
        for n in re.findall(rf'\b{d.group(1)}\.add_parser\(\s*["\']([\w-]+)["\']', path.read_text()):
            found.append(f'{parent} {n}' if parent else n)
    return found


def check_cli(root):
    cli_md = root / 'docs' / 'cli.md'
    if not cli_md.exists():
        return ['docs/cli.md: missing']
    doc = cli_md.read_text()
    return [f'docs/cli.md: subcommand `polymorph {cmd}` is not documented'
            for cmd in cli_subcommands(root) if f'polymorph {cmd}' not in doc and f'`{cmd}`' not in doc]


def check_banner(root):
    readme = root / 'README.md'
    if not readme.exists() or BANNER not in readme.read_text():
        return [f'README.md: the "{BANNER}" notice is missing']
    return []


def check_cost(root):
    problems = []
    readme = root / 'README.md'
    if not readme.exists() or not re.search(r'^## Cost and responsibility\s*$', readme.read_text(), re.M):
        problems.append('README.md: no "Cost and responsibility" heading')
    for name in ('docs/cli.md', 'docs/discovery.md'):
        path = root / name
        if not path.exists() or '](../README.md#cost-and-responsibility)' not in path.read_text():
            problems.append(f'{name}: no link to README.md#cost-and-responsibility')
    return problems


def check_paths(root):
    problems = []
    pattern = re.compile(r'(?:/home|/Users)/([A-Za-z0-9._-]+)')
    for path in files(root):
        text = text_of(path)
        if text is None:
            continue
        for m in pattern.finditer(text):
            if m.group(1) not in PLACEHOLDER_USERS:
                problems.append(f'{rel(root, path)}: real-looking home path {m.group(0)}')
                break
    return problems


CHECKS = [check_skills, check_links, check_cli, check_banner, check_cost, check_paths]


def main():
    problems = []
    for check in CHECKS:
        problems += [f'[{check.__name__[6:]}] {p}' for p in check(ROOT)]
    for p in problems:
        print(p)
    print('docs:', f'{len(problems)} problem(s)' if problems else 'ok')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
