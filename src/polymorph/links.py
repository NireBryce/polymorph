"""Create the symlinks a harness needs to read what the repository provides.

Issue #39. The gap report names a provided path a harness does not read; `no-link` is the
case where the path the harness reads does not exist at all. That is the one case a link
fixes without losing anything, so it is the only one made here. A real file or directory
(`unlinked-copy`) and a symlink to somewhere else (`points-elsewhere`) are never touched:
the first needs a person to merge two copies, the second is someone's decision.

A link is made only from a built-in coverage table. A repo-local table is data the
repository ships, and letting it choose where `equip` creates a symlink would let a cloned
repository place one anywhere under its root. Link paths under `.git` and `.polymorph` are
refused whatever the table says. Nothing is executed; paths are strings checked and linked.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .inventory import NO_LINK, Coverage, Inventory, gaps
from .resources import COVERAGE_DIR

PROTECTED = (".git", ".polymorph")


@dataclass(frozen=True)
class Link:
    """A symlink for one harness: `path` (repo-relative) points at `target` (relative text)."""

    harness: str
    path: str
    target: str
    kind: str = ""


@dataclass
class LinkPlan:
    create: list[Link] = field(default_factory=list)
    refused: list[tuple[str, str, str]] = field(default_factory=list)   # harness, path, reason


def _builtin(cap: Coverage) -> bool:
    return bool(cap.source) and Path(cap.source).resolve().parent == COVERAGE_DIR.resolve()


def _unsafe(repo: Path, rel: str) -> str | None:
    """Why a link may not be created at `rel`, or None."""
    parts = Path(rel).parts
    if parts[0] in PROTECTED:
        return f"{parts[0]}/ is never a link location"
    ancestor = repo
    root = repo.resolve()
    for part in parts[:-1]:
        ancestor = ancestor / part
        if ancestor.is_symlink() and not Path(os.path.realpath(ancestor)).is_relative_to(root):
            return f"{ancestor.relative_to(repo).as_posix()} is a symlink that leaves the repository"
        if os.path.lexists(ancestor) and not ancestor.is_dir():
            return f"{ancestor.relative_to(repo).as_posix()} is not a directory"
    return None


def plan(repo: str | Path, coverages: list[Coverage], inv: Inventory) -> LinkPlan:
    """The links that would close every `no-link` gap of the given harnesses."""
    repo = Path(repo)
    out = LinkPlan()
    wanted: list[Link] = []
    for coverage in coverages:
        for gap in gaps(inv, coverage, repo):
            if gap.reason != NO_LINK or not gap.link_to or not gap.reads:
                continue
            link = Link(coverage.name, gap.reads[0], gap.link_to, gap.kind)
            if not _builtin(coverage):
                out.refused.append((coverage.name, link.path,
                                    "the coverage table is repo-local; links come only from built-in tables"))
            elif (why := _unsafe(repo, link.path)):
                out.refused.append((coverage.name, link.path, why))
            elif link not in wanted:
                wanted.append(link)
    targets: dict[str, set[str]] = {}
    for link in wanted:
        targets.setdefault(link.path, set()).add(link.target)
    for link in wanted:
        if len(targets[link.path]) > 1:
            out.refused.append((link.harness, link.path,
                                "harnesses need different targets here: " + ", ".join(sorted(targets[link.path]))))
        else:
            out.create.append(link)
    return out


def apply(repo: str | Path, create: list[Link]) -> tuple[list[Link], list[tuple[str, str, str]]]:
    """Make the links. Returns (made, failed). A path that exists by now is failed, never replaced."""
    repo = Path(repo)
    made: list[Link] = []
    failed: list[tuple[str, str, str]] = []
    outcome: dict[str, bool] = {}
    for link in create:
        if link.path in outcome:           # two harnesses, same link: made once, recorded for both
            if outcome[link.path]:
                made.append(link)
            continue
        dest = repo / link.path
        if os.path.lexists(dest):
            failed.append((link.harness, link.path, "exists now; not replaced"))
            outcome[link.path] = False
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(link.target, dest)
        except OSError as e:
            failed.append((link.harness, link.path, str(e)))
            outcome[link.path] = False
            continue
        outcome[link.path] = True
        made.append(link)
    return made, failed
