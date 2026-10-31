#!/usr/bin/env python3
"""Merge a green PR and clean up after it.

    python3 scripts/ship_land.py PR [--timeout SECONDS] [--interval SECONDS]
    just ship-land PR

Refuses unless scripts/ci_wait.py's conditions hold (every run on the head
commit succeeded and the PR is CLEAN). Then: merge with `--rebase` when the PR
has exactly one commit, else `--merge`, pinned with `--match-head-commit` to the
head that was checked; fast-forward the local base branch; remove a worktree on
the PR's branch; delete the local and the remote branch. A remote branch that
is already gone counts as done. Prints each step it took.

Closes no issues. The caller closes them by hand and says so.

Run it from a checkout that is not the worktree being removed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_wait  # noqa: E402


def run(*cmd: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, cwd=cwd)


def worktrees() -> list[dict]:
    p = run("git", "worktree", "list", "--porcelain")
    if p.returncode != 0:
        raise ci_wait.GhError(f"git worktree list failed: {p.stderr.strip()}")
    out, cur = [], {}
    for line in p.stdout.splitlines() + [""]:
        if not line:
            if cur:
                out.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        cur[key] = val
    return out


def worktree_for(branch: str, trees: list[dict]) -> str | None:
    for t in trees:
        if t.get("branch") == f"refs/heads/{branch}":
            return t["worktree"]
    return None


def inside(path: str, cwd: str) -> bool:
    try:
        Path(cwd).resolve().relative_to(Path(path).resolve())
        return True
    except ValueError:
        return False


def land(pr: str, timeout: float, interval: float, say=print) -> int:
    # 1. read the status before anything goes outward
    r = ci_wait.wait(pr, timeout, interval)
    say(f"{r.sha[:7]} {r.status} {r.conclusion}  {r.merge_state}")
    if not r.ok:
        say(f"ship-land: refusing to merge PR {r.pr}: {r.reason}")
        return r.code or 1
    info = ci_wait.gh_json("pr", "view", pr, "--json", "baseRefName,commits")
    base, branch = info["baseRefName"], r.head_branch
    trees = worktrees()
    wt = worktree_for(branch, trees)
    if wt and inside(wt, os.getcwd()):
        say(f"ship-land: refusing: this shell is inside the worktree {wt} that would be removed; "
            "run it from another checkout. Nothing was merged.")
        return 1
    # 2. merge, pinned to the head that was checked
    mode = "--rebase" if len(info["commits"]) == 1 else "--merge"
    m = run("gh", "pr", "merge", r.pr, mode, "--match-head-commit", r.sha)
    if m.returncode != 0:
        say(f"ship-land: merge failed, nothing deleted: {m.stderr.strip() or m.stdout.strip()}")
        return 1
    say(f"merged PR {r.pr} with {mode} at head {r.sha[:7]}")
    state = ci_wait.gh_json("pr", "view", pr, "--json", "state")["state"]
    if state != "MERGED":
        say(f"ship-land: gh reported success but the PR is {state}; nothing deleted")
        return 1
    rc = 0
    # 3. sync the base branch, fast-forward only
    base_wt = worktree_for(base, trees)
    f = run("git", "fetch", "origin", base)
    if f.returncode == 0:
        if base_wt:
            s = run("git", "merge", "--ff-only", "FETCH_HEAD", cwd=base_wt)
        else:
            s = run("git", "fetch", "origin", f"{base}:{base}")
        ok = s.returncode == 0
        detail = s.stderr.strip() or s.stdout.strip()
    else:
        ok, detail = False, f.stderr.strip()
    say(f"synced {base} (--ff-only)" if ok else f"sync of {base} FAILED: {detail}")
    rc |= 0 if ok else 1
    # 4. worktree, then local branch (the branch is checked out there until it goes)
    if wt:
        w = run("git", "worktree", "remove", wt)
        say(f"removed worktree {wt}" if w.returncode == 0
            else f"worktree removal FAILED: {w.stderr.strip()}")
        rc |= 0 if w.returncode == 0 else 1
    # -D: a --rebase merge leaves the local commits "unmerged" to `git branch -d`; the PR is MERGED
    b = run("git", "branch", "-D", branch)
    say(f"deleted local branch {branch}" if b.returncode == 0
        else f"local branch delete FAILED: {b.stderr.strip()}")
    rc |= 0 if b.returncode == 0 else 1
    d = run("git", "push", "origin", "--delete", branch)
    if d.returncode == 0:
        say(f"deleted remote branch {branch}")
    elif "remote ref does not exist" in d.stderr:
        say(f"remote branch {branch} already gone")
    else:
        say(f"remote branch delete FAILED: {d.stderr.strip()}")
        rc |= 1
    say("closed no issues: close any the PR resolves by hand, and say so")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pr", help="PR number")
    ap.add_argument("--timeout", type=float, default=ci_wait.DEFAULT_TIMEOUT)
    ap.add_argument("--interval", type=float, default=ci_wait.DEFAULT_INTERVAL)
    a = ap.parse_args(argv)
    try:
        return land(a.pr, a.timeout, a.interval)
    except (ci_wait.GhError, OSError) as e:
        print(f"ship-land: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
