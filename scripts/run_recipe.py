#!/usr/bin/env python3
"""Run a justfile recipe where `just` is not installed (issue 98).

    python3 scripts/run_recipe.py                  # list the recipes
    python3 scripts/run_recipe.py preflight        # what CI runs
    python3 scripts/run_recipe.py ship-land 12 --allow-base experimental

Reads this repo's justfile, so the recipes cannot drift from it, and runs the
subset of just that file uses: a `# comment` line above a recipe is its
description; a header is `NAME [PARAM ...] [*VARIADIC]: [DEPENDENCY ...]`;
body lines are indented and each runs in `sh` from the repo root, echoed to
stderr first unless it starts with `@`; `{{PARAM}}` is replaced by the
shell-quoted argument. Dependencies run first, each recipe at most once.
The first failing line stops the run with its exit status, as just does.

Anything else just can do (settings, variables, `import`, shebang recipes,
expressions in `{{ }}`) is refused with exit 2 and the line that uses it:
put the logic in a script under scripts/ instead (scripts/self_check.py is
the worked example). tests/test_run_recipe.py parses the real justfile, so a
recipe this runner cannot run fails the unit tests.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER_RE = re.compile(r"^(?P<name>[a-z][a-z0-9-]*)(?P<params>(?: +\*?[a-z_][a-z0-9_]*)*) *:"
                       r"(?P<deps>(?: +[a-z][a-z0-9-]*)*) *$")
PLACEHOLDER_RE = re.compile(r"\{\{(.*?)\}\}")
PARAM_NAME_RE = re.compile(r"[a-z_][a-z0-9_]*")


class JustfileError(Exception):
    pass


@dataclass
class Recipe:
    name: str
    params: list[str]                 # a leading `*` marks the variadic one
    deps: list[str]
    description: str = ""
    lines: list[str] = field(default_factory=list)


def parse_justfile(text: str) -> dict[str, Recipe]:
    """The recipes in the order the file declares them; JustfileError on syntax outside the subset."""
    recipes: dict[str, Recipe] = {}
    comment_lines: list[str] = []
    current: Recipe | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        where = f"justfile:{line_number}"
        if line.startswith(("    ", "\t")):
            if current is None:
                raise JustfileError(f"{where}: indented line outside a recipe")
            body_line = line.strip()
            if not current.lines and body_line.startswith("#!"):
                raise JustfileError(f"{where}: shebang recipe {current.name!r}; move it into scripts/")
            for placeholder in PLACEHOLDER_RE.findall(body_line):
                if placeholder.strip() not in [param.lstrip("*") for param in current.params]:
                    raise JustfileError(f"{where}: {{{{{placeholder}}}}} is not a parameter of {current.name!r}")
            current.lines.append(body_line)
            continue
        current = None
        if not line.strip():
            comment_lines = []
        elif line.startswith("#"):
            comment_lines.append(line.lstrip("#").strip())
        else:
            header = HEADER_RE.match(line)
            if not header:
                raise JustfileError(f"{where}: not a recipe header this runner understands: {line!r}")
            params = header["params"].split()
            if any(param.startswith("*") for param in params[:-1]):
                raise JustfileError(f"{where}: only the last parameter may be variadic")
            current = Recipe(header["name"], params, header["deps"].split(), " ".join(comment_lines))
            if current.name in recipes:
                raise JustfileError(f"{where}: recipe {current.name!r} is declared twice")
            recipes[current.name] = current
            comment_lines = []
    for recipe in recipes.values():
        for dep in recipe.deps:
            if dep not in recipes:
                raise JustfileError(f"recipe {recipe.name!r} depends on unknown recipe {dep!r}")
            if recipes[dep].params:
                raise JustfileError(f"recipe {recipe.name!r} depends on {dep!r}, which takes parameters")
    return recipes


def bind_arguments(recipe: Recipe, args: list[str]) -> dict[str, str]:
    """Each parameter name -> its shell-quoted text; JustfileError on a wrong argument count."""
    required = [param for param in recipe.params if not param.startswith("*")]
    variadic = recipe.params[-1].lstrip("*") if recipe.params and recipe.params[-1].startswith("*") else None
    if len(args) < len(required) or (variadic is None and len(args) > len(required)):
        usage = " ".join(param.upper() if not param.startswith("*") else f"[{param[1:].upper()} ...]"
                         for param in recipe.params)
        raise JustfileError(f"usage: run_recipe.py {recipe.name} {usage}".rstrip())
    bound = {name: shlex.quote(value) for name, value in zip(required, args)}
    if variadic is not None:
        bound[variadic] = " ".join(shlex.quote(value) for value in args[len(required):])
    return bound


def render_line(line: str, bound: dict[str, str]) -> str:
    return PLACEHOLDER_RE.sub(lambda match: bound[match.group(1).strip()], line)


def run(recipes: dict[str, Recipe], name: str, args: list[str], already_run: set[str] | None = None) -> int:
    already_run = set() if already_run is None else already_run
    recipe = recipes[name]
    bound = bind_arguments(recipe, args)
    for dep in recipe.deps:
        if dep not in already_run:
            rc = run(recipes, dep, [], already_run)
            if rc != 0:
                return rc
    already_run.add(name)
    for line_number, line in enumerate(recipe.lines, start=1):
        quiet = line.startswith("@")
        command = render_line(line[1:] if quiet else line, bound)
        if not quiet:
            print(command, file=sys.stderr, flush=True)
        rc = subprocess.run(command, shell=True, cwd=ROOT).returncode
        if rc != 0:
            print(f"error: recipe `{name}` failed on line {line_number} with exit code {rc}", file=sys.stderr)
            return rc
    return 0


def listing(recipes: dict[str, Recipe]) -> str:
    rows = [(" ".join([recipe.name, *recipe.params]), recipe.description)
            for recipe in recipes.values() if recipe.name != "default"]
    width = max(len(signature) for signature, _ in rows)
    return "\n".join(f"    {signature.ljust(width)} # {description}" if description else f"    {signature}"
                     for signature, description in rows)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        recipes = parse_justfile((ROOT / "justfile").read_text())
        if not argv or argv[0] in ("-l", "--list", "default"):
            print("Recipes (run with python3 scripts/run_recipe.py NAME):")
            print(listing(recipes))
            return 0
        if argv[0] not in recipes:
            raise JustfileError(f"no recipe {argv[0]!r}; run with no arguments to list them")
        return run(recipes, argv[0], argv[1:])
    except JustfileError as error:
        print(f"run_recipe: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
