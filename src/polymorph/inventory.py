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

import os
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


# Why a provided path is not reached by a harness.
UNSUPPORTED = "unsupported"          # the harness has no recorded way to read this kind at all
NO_LINK = "no-link"                  # the harness reads a path that does not exist; the content lives elsewhere
UNLINKED_COPY = "unlinked-copy"      # the harness reads a real file or dir that is a second copy (drift)
POINTS_ELSEWHERE = "points-elsewhere"  # the harness reads a symlink that leads somewhere else
REASONS = (UNSUPPORTED, NO_LINK, UNLINKED_COPY, POINTS_ELSEWHERE)


@dataclass(frozen=True)
class Gap:
    """A provided path the harness will not read, and why.

    reads: the path(s) the harness looks at for this kind (empty when unsupported);
    fix: what would make it reach the provided path, as text;
    link_to: for `no-link`, the relative target of the symlink at reads[0] that closes the gap
    (what `equip` and `adopt` create); empty for every other reason.
    """

    harness: str
    kind: str
    path: str
    reason: str = UNSUPPORTED
    reads: tuple[str, ...] = ()
    fix: str = ""
    link_to: str = ""


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
    source_path = repo / source
    if not source_path.is_dir():
        return []
    return sorted(source_path.iterdir(), key=lambda entry: entry.name)


def read_inventory(repo: str | Path = ".") -> Inventory:
    """Discover what the repo provides for each discover-and-report kind.

    Reads directory structure and symlink targets only; file contents are
    never opened. A source directory that is itself a symlink (the basecamp
    store) is read through, and each item's resolved target records that.
    """
    repo = Path(repo)

    def items(kind: str, entries: list[Path]) -> tuple[Item, ...]:
        return tuple(_item(repo, kind, entry) for entry in entries)

    return Inventory(
        skills=items("skills", _children(repo, SKILLS_SOURCE)),
        rules=items("rules", sum((_children(repo, rules_source) for rules_source in RULES_SOURCES), [])),
        memory=items("memory", [repo / filename for filename in MEMORY_FILES
                                if (repo / filename).is_symlink() or (repo / filename).is_file()]),
        claude_dir=items("claude_dir", _children(repo, CLAUDE_DIR)),
    )


def _path_ok(value: str) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    parts = PurePosixPath(value).parts
    if ".." in parts or any(part in ("", ".") for part in parts):
        return False
    return not any(char in part for part in parts for char in "$`\\\"'<>|;&*?!")


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
    inventory_table = raw.get("inventory", {})
    if not isinstance(inventory_table, dict):
        raise InventoryError(f"{source}: [inventory] must be a table")
    unknown_kinds = sorted(set(inventory_table) - set(CAP_KEYS))
    if unknown_kinds:
        raise InventoryError(f"{source}: unknown inventory kind(s) {', '.join(unknown_kinds)}")
    memory = inventory_table.get("memory", [])
    if isinstance(memory, str):
        raise InventoryError(f"{source}: memory must be a list of file names")
    for memory_file in memory:
        if not isinstance(memory_file, str) or not memory_file or not all(char.isalnum() or char in "._-" for char in memory_file):
            raise InventoryError(f"{source}: memory entry {memory_file!r} is not a plain file name")
    for key in ("skills", "rules", "claude_dir"):
        if key in inventory_table and not _path_ok(inventory_table[key]):
            raise InventoryError(f"{source}: inventory.{key} {inventory_table[key]!r} is not a plain relative path")
    return Coverage(
        name=name,
        skills=inventory_table.get("skills"),
        rules=inventory_table.get("rules"),
        memory=tuple(memory),
        claude_dir=inventory_table.get("claude_dir"),
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

    if not name or not all(char.isalnum() or char in "_-" for char in name):
        raise InventoryError(f"bad harness name {name!r}")
    repo = Path(repo)
    builtin = COVERAGE_DIR
    repo_dir = repo / REPO_DIR
    sources: list[Path] = []
    if coverage_dir:
        coverage_path = Path(coverage_dir)
        allowed = (repo_dir.resolve(), builtin.resolve(), *(Path(trust_dir).resolve() for trust_dir in trust_dirs))
        if coverage_path.resolve() not in allowed:
            raise InventoryError(f"--coverage {coverage_dir} is outside trusted dirs")
        sources.append(coverage_path / f"{name}.toml")
    sources.append(repo_dir / f"{name}.toml")
    sources.append(builtin / f"{name}.toml")
    for path in sources:
        if path.is_file():
            raw = tomllib.loads(path.read_text())
            coverage = parse_coverage(raw, str(path))
            if coverage.name != name:
                raise InventoryError(f"{path}: name {coverage.name!r} does not match {name!r}")
            if path.is_relative_to(repo_dir):
                warn(f"warning: {path} overrides the built-in '{name}' coverage table")
            return coverage
    return None


def available_tables(repo: str | Path = ".") -> list[str]:
    """Every harness with a coverage table, repo-local first, then built-in."""
    repo = Path(repo)
    builtin = COVERAGE_DIR
    names: dict[str, None] = {}
    for table_dir in (repo / REPO_DIR, builtin):
        if table_dir.is_dir():
            for table_path in sorted(table_dir.glob("*.toml")):
                names.setdefault(table_path.stem, None)
    return sorted(names)


def _read_paths(cap: Coverage, kind: str) -> tuple[str, ...]:
    """The repo-relative paths a harness reads for a kind; empty when it reads none."""
    if kind == "memory":
        return tuple(cap.memory)
    value = getattr(cap, kind)
    return (value,) if value is not None else ()


def _same(a: Path, b: Path) -> bool:
    return a.exists() and b.exists() and a.resolve() == b.resolve()


def _reached(repo: Path, kind: str, item: Item, read: str) -> bool:
    """Does the harness's read path lead to this item? Files compare by resolved path; for the
    directory kinds an item is reached when the directory holding it is the one the harness reads."""
    target, read_path = repo / item.path, repo / read
    if kind == "memory":
        return _same(target, read_path)
    return _same(target.parent, read_path)


def _link_target(repo: Path, kind: str, item: Item, read: str) -> str:
    """The relative target of a symlink at `read` that makes it reach the item."""
    source = repo / item.path
    if kind != "memory":
        source = source.parent
    return os.path.relpath(source, (repo / read).parent)


def _honoured_target(repo: Path, cap: Coverage, kind: str, item: Item) -> bool:
    """True when the item is a symlink whose resolved target is honoured content: a pointer,
    not a gap, because the harness reads those bytes through the other path (issue #31).

    A path is honoured when it is a memory file the table names, or lies inside (or is) a
    directory the table honours for some kind -- `.claude/skills` -> `.agents/skills` when
    the table reads skills there. A memory item counts as honoured only through `_reached`,
    which already compares resolved paths; a memory symlink pointing anywhere else (or at a
    file only honoured as another kind) stays a gap.
    """
    if item.target is None or kind == "memory":
        return False
    resolved_target = (repo / item.target).resolve()
    if any(_same(repo / item.target, repo / memory_read) for memory_read in cap.memory):
        return True
    for honoured_dir in (cap.skills, cap.rules, cap.claude_dir):
        if honoured_dir:
            resolved_dir = (repo / honoured_dir).resolve()
            if resolved_target == resolved_dir or resolved_dir in resolved_target.parents:
                return True
    return False


def gaps(inventory: Inventory, coverage: Coverage, repo: str | Path) -> list[Gap]:
    """Provided paths the harness will not read, with the reason.

    A harness reads fixed paths (the coverage table). An item is reached when one of them
    resolves to it, directly or through a symlink. A symlink whose resolved target is
    honoured content is a pointer, not a gap (`_honoured_target`). Otherwise the reason says
    what stands there instead: nothing (`no-link`), a second real copy (`unlinked-copy`, the
    usual source of drift between CLAUDE.md and AGENTS.md or .claude/ and .agents/), or a
    symlink to something else (`points-elsewhere`). A kind the harness has no path for is
    `unsupported`.
    """
    repo = Path(repo)
    out: list[Gap] = []
    for kind in KINDS:
        reads = _read_paths(coverage, kind)
        for item in inventory.kind(kind):
            if any(_reached(repo, kind, item, candidate_read) for candidate_read in reads):
                continue
            if _honoured_target(repo, coverage, kind, item):
                continue
            if not reads:
                out.append(Gap(coverage.name, kind, item.path, UNSUPPORTED))
                continue
            present = [candidate_read for candidate_read in reads
                       if (repo / candidate_read).exists() or (repo / candidate_read).is_symlink()]
            if not present:
                reason = NO_LINK
            elif any((repo / candidate_read).is_symlink() for candidate_read in present):
                reason = POINTS_ELSEWHERE
            else:
                reason = UNLINKED_COPY
            links = [candidate_read for candidate_read in present if (repo / candidate_read).is_symlink()]
            read_path = (links or present or list(reads))[0]
            item_location = item.path if kind == "memory" else PurePosixPath(item.path).parent.as_posix()
            link_to = ""
            if reason == NO_LINK:
                link_to = _link_target(repo, kind, item, read_path)
                fix = f"polymorph equip --harness {coverage.name} links {read_path} -> {link_to}"
            elif reason == UNLINKED_COPY:
                fix = f"{read_path} and {item_location} are separate copies: merge them into one and link the other to it"
            else:
                fix = f"{read_path} is a symlink to {os.readlink(repo / read_path)}, not to {item_location}"
            out.append(Gap(coverage.name, kind, item.path, reason, reads=reads, fix=fix, link_to=link_to))
    return out
