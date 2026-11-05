"""adopt: move a repo's agent files at rest into the basecamp store.

Issue #24. The store (`.polymorph/basecamp/`) holds the canonical copy of
each core agent file; the agent-facing path becomes a relative symlink into
it, so a link path is never a second copy. Refusals are the feature: a path
that is already a symlink is left alone and reported, uncommitted changes
refuse the move (there is no safety net without a clean tree), and hook
scripts are never touched -- the trust model names them where they are.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import links as linkmod

STORE = ".polymorph/basecamp"

# (kind, at-rest path, store path). Hook scripts and settings.json are
# deliberately absent: they stay where the harness reads them.
CANDIDATES: tuple[tuple[str, str, str], ...] = (
    ("memory", "AGENTS.md", "AGENTS.md"),
    ("memory", "CLAUDE.md", "CLAUDE.md"),
    ("skills", ".agents/skills", "skills"),
    ("rules", ".claude/rules", "rules/claude"),
    ("rules", ".agents/rules", "rules/agents"),
)


class AdoptError(ValueError):
    """Raised when adopt cannot run at all."""


@dataclass(frozen=True)
class Action:
    kind: str
    path: str
    store: str


@dataclass
class AdoptResult:
    moved: list[Action] = field(default_factory=list)
    already: list[tuple[str, str, str]] = field(default_factory=list)  # kind, path, points-to
    absent: list[tuple[str, str]] = field(default_factory=list)        # kind, path
    refused: list[tuple[str, str, str]] = field(default_factory=list)  # kind, path, reason
    linked: list[linkmod.Link] = field(default_factory=list)           # made (dry run: would be made)
    link_refused: list[tuple[str, str, str]] = field(default_factory=list)  # harness, path, reason
    dry_run: bool = False

    def ok(self) -> bool:
        return not self.refused and not self.link_refused


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _dirty(repo: Path, rel: str) -> str | None:
    """A reason string when git shows uncommitted changes for rel, else None."""
    status_proc = _git(repo, "status", "--porcelain", "--ignored", "--", rel)
    if status_proc.returncode != 0:
        return f"git status failed ({status_proc.stderr.strip() or 'not a git repository'}); refusing without a safety net"
    if any(line.startswith("!!") for line in status_proc.stdout.splitlines()):
        return "ignored by git (no committed copy to fall back on)"
    if status_proc.stdout.strip():
        return "uncommitted changes"
    return None


def _link_harnesses(repo: Path, result: AdoptResult, dry_run: bool) -> None:
    """Close every `no-link` gap of every harness that has a coverage table (issue #39)."""
    from .equip import record_links
    from .inventory import available_tables, load_coverage, read_inventory

    coverage_tables = [coverage for name in available_tables(repo) if (coverage := load_coverage(name, repo)) is not None]
    link_plan = linkmod.plan(repo, coverage_tables, read_inventory(repo))
    result.link_refused = list(link_plan.refused)
    if dry_run:
        result.linked = link_plan.create
        return
    result.linked, failed = linkmod.apply(repo, link_plan.create)
    result.link_refused += failed
    if result.linked:
        record_links(repo, result.linked)


def adopt(repo: str | Path = ".", dry_run: bool = False, make_links: bool = True) -> AdoptResult:
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise AdoptError(f"no such directory: {repo}")
    store = repo / STORE
    result = AdoptResult(dry_run=dry_run)
    for kind, at_rest, store_rel in CANDIDATES:
        at_rest_path = repo / at_rest
        store_path = store / store_rel
        if not at_rest_path.exists() and not at_rest_path.is_symlink():
            result.absent.append((kind, at_rest))
            continue
        if at_rest_path.is_symlink():
            result.already.append((kind, at_rest, os.readlink(at_rest_path)))
            continue
        if store_path.exists() or store_path.is_symlink():
            result.refused.append((kind, at_rest, f"both copies exist: {STORE}/{store_rel} and {at_rest}"))
            continue
        reason = _dirty(repo, at_rest)
        if reason:
            result.refused.append((kind, at_rest, reason))
            continue
        if dry_run:
            result.moved.append(Action(kind=kind, path=at_rest, store=f"{STORE}/{store_rel}"))
            continue
        store_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(at_rest_path), str(store_path))
        link = os.path.relpath(store_path, at_rest_path.parent)
        os.symlink(link, at_rest_path)
        result.moved.append(Action(kind=kind, path=at_rest, store=f"{STORE}/{store_rel}"))
    if make_links:
        _link_harnesses(repo, result, dry_run)
    return result
