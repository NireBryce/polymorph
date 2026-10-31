#!/usr/bin/env python3
"""Merge a green PR and clean up after it.

    python3 scripts/ship_land.py PR [--timeout SECONDS] [--interval SECONDS] [--allow-base BRANCH]
    just ship-land PR

Refuses unless scripts/ci_wait.py's conditions hold (every run on the head
commit succeeded and the PR is CLEAN), and, before merging anything, refuses a PR
whose head is in another repository, whose head branch is the base or a protected
branch (main, master, experimental, the repo's default branch), or whose base is
not the repo's default branch (`--allow-base BRANCH` permits a stacked PR onto
BRANCH). Then: merge with `--rebase` when the PR
has exactly one commit, else `--merge`, pinned with `--match-head-commit` to the
head that was checked; fast-forward the local base branch; remove a worktree on
the PR's branch; delete the local branch (`-d`; `-D` only when it is exactly the head that
was checked) and the remote branch (not while another open PR is based on it). A
remote branch that is already gone counts as done. Prints each step it took.

Closes no issues. The caller closes them by hand and says so.

Run it from a checkout that is not the worktree being removed.
"""
from __future__ import annotations

import argparse
import json
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


PROTECTED = ("main", "master", "experimental")


def refusal(r, default: str, allow_base: str | None) -> str | None:
    """Why this PR must not be landed (and its branch deleted), or None."""
    if r.cross_repo:
        return f"the head of PR {r.pr} is in another repository (or that could not be told)"
    if r.head_branch == r.base_branch:
        return f"the head branch {r.head_branch!r} is the same as the base"
    if r.head_branch in PROTECTED or r.head_branch == default:
        return f"the head branch {r.head_branch!r} is protected ({', '.join(sorted({*PROTECTED, default}))})"
    if r.base_branch != default and r.base_branch != allow_base:
        return (f"the base is {r.base_branch!r}, not the default branch {default!r}; "
                f"pass --allow-base {r.base_branch} for a stacked PR")
    return None


def land(pr: str, timeout: float, interval: float, say=print, allow_base: str | None = None) -> int:
    # 1. read the status before anything goes outward
    r = ci_wait.wait(pr, timeout, interval)
    say(f"{r.sha[:7]} {r.status} {r.conclusion}  {r.merge_state}")
    if not r.ok:
        say(f"ship-land: refusing to merge PR {r.pr}: {r.reason}")
        return r.code or 1
    default = ci_wait.gh_json("repo", "view", "--json", "defaultBranchRef")["defaultBranchRef"]["name"]
    why = refusal(r, default, allow_base)
    if why:
        say(f"ship-land: refusing: {why}. Nothing was merged.")
        return 1
    info = ci_wait.gh_json("pr", "view", pr, "--json", "commits")
    base, branch = r.base_branch, r.head_branch
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
    # -d first; a --rebase merge leaves the local commits "unmerged" to it, so -D only when the
    # local branch is exactly the head that was verified and merged
    b = run("git", "branch", "-d", branch)
    if b.returncode != 0:
        local = run("git", "rev-parse", f"refs/heads/{branch}")
        if local.returncode == 0 and local.stdout.strip() == r.sha:
            b = run("git", "branch", "-D", branch)
        else:
            say(f"left local branch {branch}: it is not the verified head {r.sha[:7]} "
                f"({b.stderr.strip()}); delete it by hand if it is not wanted")
            b = None
    if b is not None:
        say(f"deleted local branch {branch}" if b.returncode == 0
            else f"local branch delete FAILED: {b.stderr.strip()}")
        rc |= 0 if b.returncode == 0 else 1
    dependents = run("gh", "pr", "list", "--base", branch, "--state", "open", "--json", "number")
    try:
        dependent = json.loads(dependents.stdout) if dependents.returncode == 0 else None
    except ValueError:
        dependent = None
    if dependent is None:
        say(f"remote branch {branch} NOT deleted: could not list open PRs based on it")
        rc |= 1
    elif dependent:
        nums = ", ".join(f"#{p.get('number')}" for p in dependent)
        say(f"remote branch {branch} NOT deleted: open PR {nums} is based on it; retarget it first")
    else:
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
    ap.add_argument("--allow-base", metavar="BRANCH",
                    help="permit a PR whose base is BRANCH instead of the default branch (stacked PRs)")
    a = ap.parse_args(argv)
    try:
        return land(a.pr, a.timeout, a.interval, allow_base=a.allow_base)
    except (ci_wait.GhError, OSError) as e:
        print(f"ship-land: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
