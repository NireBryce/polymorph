"""The discover-and-report inventory: skills, rules, memory, .claude.

Slice 1 of the loadout pivot (docs/plan-loadout-pivot.md). The transliterate
kinds (settings, hooks) are the reader's job; these kinds are discovered as
data and never executed. Per-harness coverage tables -- also data, the same
character as the mappings -- say what a harness honours; the gap report names
what the repo provides that a harness will silently ignore even after
equipping. A coverage table that is absent means nothing is known about
that harness: absence is reported as unknown, never as a gap.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .resources import COVERAGE_DIR

REPO_DIR = ".polymorph/basecamp/coverage"
KINDS = ("skills", "rules", "memory", "claude_dir")
CAP_KEYS = KINDS
SKILLS_SOURCE = ".agents/skills"
RULES_SOURCES = (".claude/rules", ".agents/rules")
MEMORY_FILES = ("AGENTS.md", "CLAUDE.md")
CLAUDE_DIR = ".claude"


class InventoryError(ValueError):
    """Raised when the inventory or a coverage table cannot be read."""


@dataclass(frozen=True)
class Item:
    """One discovered file or directory, repo-relative with forward slashes.

    target is the real file the path resolves to when it is a symlink (also
    repo-relative when it stays inside the repo), else None.
    """

    kind: str
    path: str
    target: str | None = None


@dataclass(frozen=True)
class Inventory:
    skills: tuple[Item, ...] = ()
    rules: tuple[Item, ...] = ()
    memory: tuple[Item, ...] = ()
    claude_dir: tuple[Item, ...] = ()

    def kind(self, kind: str) -> tuple[Item, ...]:
        return getattr(self, kind)


@dataclass(frozen=True)
class Coverage:
    """What one harness honours of the discover-and-report kinds.

    A kind absent here means the harness is not known to honour it.
    """

    name: str
    skills: str | None = None
    rules: str | None = None
    memory: tuple[str, ...] = ()
    claude_dir: str | None = None
    source: str = ""


@dataclass(frozen=True)
class Gap:
    """A provided path the harness has no recorded way of honouring."""

    harness: str
    kind: str
    path: str


def _rel(repo: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(p.resolve())


def _item(repo: Path, kind: str, path: Path) -> Item:
    target = None
    if path.is_symlink():
        target = _rel(repo, path)
    return Item(kind=kind, path=path.relative_to(repo).as_posix(), target=target)


def _children(repo: Path, source: str) -> list[Path]:
    d = repo / source
    if not d.is_dir():
        return []
    return sorted(d.iterdir(), key=lambda p: p.name)


def read_inventory(repo: str | Path = ".") -> Inventory:
    """Discover what the repo provides for each discover-and-report kind.

    Reads directory structure and symlink targets only; file contents are
    never opened. A source directory that is itself a symlink (the basecamp
    store) is read through, and each item's resolved target records that.
    """
    repo = Path(repo)

    def items(kind: str, entries: list[Path]) -> tuple[Item, ...]:
        return tuple(_item(repo, kind, p) for p in entries)

    return Inventory(
        skills=items("skills", _children(repo, SKILLS_SOURCE)),
        rules=items("rules", sum((_children(repo, s) for s in RULES_SOURCES), [])),
        memory=items("memory", [repo / n for n in MEMORY_FILES
                                if (repo / n).is_symlink() or (repo / n).is_file()]),
        claude_dir=items("claude_dir", _children(repo, CLAUDE_DIR)),
    )


def _path_ok(value: str) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    parts = PurePosixPath(value).parts
    if ".." in parts or any(p in ("", ".") for p in parts):
        return False
    return not any(ch in p for p in parts for ch in "$`\\\"'<>|;&*?!")


def parse_coverage(raw: dict, source: str = "") -> Coverage:
    if not isinstance(raw, dict):
        raise InventoryError(f"{source or 'coverage table'}: not a table")
    unknown = sorted(set(raw) - {"schema", "name", "inventory"})
    if unknown:
        raise InventoryError(f"{source}: unknown key(s) {', '.join(unknown)}")
    if raw.get("schema") != 1 or isinstance(raw.get("schema"), bool):
        raise InventoryError(f"{source}: schema must be 1")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise InventoryError(f"{source}: name must be a non-empty string")
    inv = raw.get("inventory", {})
    if not isinstance(inv, dict):
        raise InventoryError(f"{source}: [inventory] must be a table")
    bad = sorted(set(inv) - set(CAP_KEYS))
    if bad:
        raise InventoryError(f"{source}: unknown inventory kind(s) {', '.join(bad)}")
    memory = inv.get("memory", [])
    if isinstance(memory, str):
        raise InventoryError(f"{source}: memory must be a list of file names")
    for m in memory:
        if not isinstance(m, str) or not m or not all(c.isalnum() or c in "._-" for c in m):
            raise InventoryError(f"{source}: memory entry {m!r} is not a plain file name")
    for key in ("skills", "rules", "claude_dir"):
        if key in inv and not _path_ok(inv[key]):
            raise InventoryError(f"{source}: inventory.{key} {inv[key]!r} is not a plain relative path")
    return Coverage(
        name=name,
        skills=inv.get("skills"),
        rules=inv.get("rules"),
        memory=tuple(memory),
        claude_dir=inv.get("claude_dir"),
        source=source,
    )


def _warn_stderr(msg: str) -> None:
    import sys

    print(msg, file=sys.stderr)


def load_coverage(name: str, repo: str | Path = ".", coverage_dir: str | None = None,
                      trust_dirs: tuple[str, ...] = (), warn=None) -> Coverage | None:
    """Load the coverage table for a harness; None when none exists.

    Lookup mirrors load_mapping: an explicit dir only from the repo dir, the
    built-in dir or a trusted dir; then the repo-local override wins over the
    built-in with a warning. A missing table is normal (nothing is known);
    an invalid one raises.
    """
    if warn is None:
        warn = _warn_stderr

    if not name or not all(c.isalnum() or c in "_-" for c in name):
        raise InventoryError(f"bad harness name {name!r}")
    repo = Path(repo)
    builtin = COVERAGE_DIR
    repo_dir = repo / REPO_DIR
    sources: list[Path] = []
    if coverage_dir:
        d = Path(coverage_dir)
        allowed = (repo_dir.resolve(), builtin.resolve(), *(Path(t).resolve() for t in trust_dirs))
        if d.resolve() not in allowed:
            raise InventoryError(f"--coverage {coverage_dir} is outside trusted dirs")
        sources.append(d / f"{name}.toml")
    sources.append(repo_dir / f"{name}.toml")
    sources.append(builtin / f"{name}.toml")
    for path in sources:
        if path.is_file():
            raw = tomllib.loads(path.read_text())
            cap = parse_coverage(raw, str(path))
            if cap.name != name:
                raise InventoryError(f"{path}: name {cap.name!r} does not match {name!r}")
            if path.is_relative_to(repo_dir):
                warn(f"warning: {path} overrides the built-in '{name}' coverage table")
            return cap
    return None


def available_tables(repo: str | Path = ".") -> list[str]:
    """Every harness with a coverage table, repo-local first, then built-in."""
    repo = Path(repo)
    builtin = COVERAGE_DIR
    names: dict[str, None] = {}
    for d in (repo / REPO_DIR, builtin):
        if d.is_dir():
            for p in sorted(d.glob("*.toml")):
                names.setdefault(p.stem, None)
    return sorted(names)


def _honours(cap: Coverage, kind: str, item: Item) -> bool:
    if kind == "memory":
        return PurePosixPath(item.path).name in cap.memory
    return getattr(cap, kind) is not None


def gaps(inv: Inventory, cap: Coverage) -> list[Gap]:
    """Provided paths the coverage table gives no way of honouring."""
    out: list[Gap] = []
    for kind in KINDS:
        for item in inv.kind(kind):
            if not _honours(cap, kind, item):
                out.append(Gap(harness=cap.name, kind=kind, path=item.path))
    return out
