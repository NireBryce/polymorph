"""The env contract: host facts in, shell exports out. Emit-only.

Slice 2 of the loadout pivot (docs/plan-loadout-pivot.md). Host facts live in
`.polymorph/basecamp/env.toml` -- machine data, not the tool's: the agent
key's rendered path, the sockets a harness must not inherit, and per-harness
session markers. The contract is emitted as shell exports (or a command is
run under them). Emit-only means the key is referenced by path and its
contents are never read, printed or moved; nothing is written anywhere; there
is no daemon. Harness names come from the host facts and the mappings, never
a hardcoded list.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

ENV_FILE = ".polymorph/basecamp/env.toml"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HARNESS_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class EnvError(ValueError):
    """Raised when env.toml is missing, invalid, or names an unknown harness."""


@dataclass(frozen=True)
class KeyFacts:
    path: str
    scope: str | None = None


@dataclass(frozen=True)
class EnvFacts:
    key: KeyFacts | None
    unset: tuple[str, ...] = ()
    harness: dict[str, tuple[str, ...]] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.harness is None:
            object.__setattr__(self, "harness", {})

    def exports_for(self, harness: str | None) -> tuple[str, ...]:
        if harness is None:
            return ()
        return self.harness.get(harness, ())


def _one_line(value: str, what: str, source: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value:
        raise EnvError(f"{source}: {what} must be a non-empty single line")
    return value


def parse_env(raw: dict, source: str = "") -> EnvFacts:
    """Validate a parsed env.toml (closed-world) into EnvFacts."""
    if not isinstance(raw, dict):
        raise EnvError(f"{source or 'env.toml'}: not a table")
    unknown = sorted(set(raw) - {"schema", "key", "vault", "harness"})
    if unknown:
        raise EnvError(f"{source}: unknown key(s) {', '.join(unknown)}")
    if raw.get("schema") != 1 or isinstance(raw.get("schema"), bool):
        raise EnvError(f"{source}: schema must be 1")

    key = None
    if "key" in raw:
        t = raw["key"]
        if not isinstance(t, dict):
            raise EnvError(f"{source}: [key] must be a table")
        bad = sorted(set(t) - {"path", "scope"})
        if bad:
            raise EnvError(f"{source}: unknown [key] entry(ies) {', '.join(bad)}")
        if "path" not in t:
            raise EnvError(f"{source}: [key] needs path")
        key = KeyFacts(path=_one_line(t["path"], "[key] path", source),
                       scope=_one_line(t["scope"], "[key] scope", source) if "scope" in t else None)

    unset: tuple[str, ...] = ()
    if "vault" in raw:
        t = raw["vault"]
        if not isinstance(t, dict):
            raise EnvError(f"{source}: [vault] must be a table")
        bad = sorted(set(t) - {"unset"})
        if bad:
            raise EnvError(f"{source}: unknown [vault] entry(ies) {', '.join(bad)}")
        names = t.get("unset", [])
        if not isinstance(names, list) or not all(isinstance(n, str) and _ENV_NAME.match(n) for n in names):
            raise EnvError(f"{source}: [vault] unset must be a list of environment variable names")
        unset = tuple(names)

    harness: dict[str, tuple[str, ...]] = {}
    if "harness" in raw:
        t = raw["harness"]
        if not isinstance(t, dict):
            raise EnvError(f"{source}: [harness] must be a table")
        for name, table in sorted(t.items()):
            if not isinstance(name, str) or not _HARNESS_NAME.match(name):
                raise EnvError(f"{source}: [harness.{name}] is not a harness name")
            if not isinstance(table, dict):
                raise EnvError(f"{source}: [harness.{name}] must be a table")
            bad = sorted(set(table) - {"exports"})
            if bad:
                raise EnvError(f"{source}: unknown [harness.{name}] entry(ies) {', '.join(bad)}")
            exports = table.get("exports", [])
            for e in exports:
                if not isinstance(e, str) or "=" not in e or not _ENV_NAME.match(e.split("=", 1)[0]):
                    raise EnvError(f"{source}: [harness.{name}] export {e!r} is not NAME=value")
                if "\n" in e:
                    raise EnvError(f"{source}: [harness.{name}] export {e.split('=', 1)[0]} must be one line")
            harness[name] = tuple(exports)

    return EnvFacts(key=key, unset=unset, harness=harness)


def load_env(repo: str | Path = ".") -> EnvFacts:
    """Read and validate `.polymorph/basecamp/env.toml`."""
    path = Path(repo) / ENV_FILE
    if not path.is_file():
        raise EnvError(f"no env contract at {ENV_FILE} (host facts are the tool's input, not its output)")
    return parse_env(tomllib.loads(path.read_text()), str(path))


def contract_lines(facts: EnvFacts, harness: str | None = None) -> list[str]:
    """The contract as valid shell, one statement per line, comments included."""
    head = f"# polymorph env contract -- harness: {harness}" if harness else "# polymorph env contract"
    lines = [head]
    if facts.key:
        scope = f" (scope: {facts.key.scope})" if facts.key.scope else ""
        lines.append(f"# agent key: {facts.key.path}{scope}")
    if facts.unset:
        lines.append("unset " + " ".join(sorted(facts.unset)))
    if facts.key:
        cmd = f"ssh -i {facts.key.path} -o IdentitiesOnly=yes"
        lines.append(f"export GIT_SSH_COMMAND={shlex.quote(cmd)}")
    for e in sorted(facts.exports_for(harness)):
        name, value = e.split("=", 1)
        lines.append(f"export {name}={shlex.quote(value)}")
    return lines


def _harness_known(repo: Path, name: str, facts: EnvFacts) -> bool:
    if name in facts.harness:
        return True
    from .mapping import MappingError, load_mapping

    try:
        load_mapping(name, repo)
        return True
    except MappingError:
        return False


def run_exec(repo: str | Path, harness: str | None, cmd: str) -> int:
    """Run cmd under `sh -c` with the contract applied to the environment."""
    repo_path = Path(repo)
    facts = load_env(repo_path)
    if harness is not None and not _harness_known(repo_path, harness, facts):
        raise EnvError(f"unknown harness {harness!r}: no [harness.{harness}] in {ENV_FILE} and no mapping")
    env = os.environ.copy()
    for name in facts.unset:
        env.pop(name, None)
    if facts.key:
        env["GIT_SSH_COMMAND"] = f"ssh -i {facts.key.path} -o IdentitiesOnly=yes"
    for e in facts.exports_for(harness):
        name, value = e.split("=", 1)
        env[name] = value
    return subprocess.run(["sh", "-c", cmd], env=env).returncode
