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
    dry_run: bool = False

    def ok(self) -> bool:
        return not self.refused


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _dirty(repo: Path, rel: str) -> str | None:
    """A reason string when git shows uncommitted changes for rel, else None."""
    p = _git(repo, "status", "--porcelain", "--ignored", "--", rel)
    if p.returncode != 0:
        return f"git status failed ({p.stderr.strip() or 'not a git repository'}); refusing without a safety net"
    if any(line.startswith("!!") for line in p.stdout.splitlines()):
        return "ignored by git (no committed copy to fall back on)"
    if p.stdout.strip():
        return "uncommitted changes"
    return None


def _rel_symlink(repo: Path, link: Path, store_rel: str) -> str:
    target_abs = repo / store_rel
    return os.path.relpath(target_abs, link.parent)


def adopt(repo: str | Path = ".", dry_run: bool = False) -> AdoptResult:
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise AdoptError(f"no such directory: {repo}")
    store = repo / STORE
    result = AdoptResult(dry_run=dry_run)
    for kind, at_rest, store_rel in CANDIDATES:
        src = repo / at_rest
        dst = store / store_rel
        if not src.exists() and not src.is_symlink():
            result.absent.append((kind, at_rest))
            continue
        if src.is_symlink():
            result.already.append((kind, at_rest, os.readlink(src)))
            continue
        if dst.exists() or dst.is_symlink():
            result.refused.append((kind, at_rest, f"both copies exist: {STORE}/{store_rel} and {at_rest}"))
            continue
        reason = _dirty(repo, at_rest)
        if reason:
            result.refused.append((kind, at_rest, reason))
            continue
        if dry_run:
            result.moved.append(Action(kind=kind, path=at_rest, store=f"{STORE}/{store_rel}"))
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        link = os.path.relpath(dst, src.parent)
        os.symlink(link, src)
        result.moved.append(Action(kind=kind, path=at_rest, store=f"{STORE}/{store_rel}"))
    return result
