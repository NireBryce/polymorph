#!/usr/bin/env python3
"""Merge a green PR and clean up after it.

    python3 scripts/ship_land.py PR [--timeout SECONDS] [--interval SECONDS] [--allow-base BRANCH]
    just ship-land PR

Refuses unless scripts/ci_wait.py's conditions hold (every run on the head
commit succeeded and the PR is CLEAN), and, before merging anything, refuses a PR
whose head is in another repository, whose head branch is the base or a protected
branch (main, master, experimental, the repo's default branch), or whose base is
not the repo's default branch (`--allow-base BRANCH` permits BRANCH: `experimental`
for the per-task flow, or a stacked PR's base). Then: merge with `--rebase` when the PR
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
    proc = run("git", "worktree", "list", "--porcelain")
    if proc.returncode != 0:
        raise ci_wait.GhError(f"git worktree list failed: {proc.stderr.strip()}")
    out, current_tree = [], {}
    for line in proc.stdout.splitlines() + [""]:
        if not line:
            if current_tree:
                out.append(current_tree)
            current_tree = {}
            continue
        key, _, val = line.partition(" ")
        current_tree[key] = val
    return out


def worktree_for(branch: str, trees: list[dict]) -> str | None:
    for tree in trees:
        if tree.get("branch") == f"refs/heads/{branch}":
            return tree["worktree"]
    return None


def inside(path: str, cwd: str) -> bool:
    try:
        Path(cwd).resolve().relative_to(Path(path).resolve())
        return True
    except ValueError:
        return False


PROTECTED = ("main", "master", "experimental")


def refusal(result, default: str, allow_base: str | None) -> str | None:
    """Why this PR must not be landed (and its branch deleted), or None."""
    if result.cross_repo:
        return f"the head of PR {result.pr} is in another repository (or that could not be told)"
    if result.head_branch == result.base_branch:
        return f"the head branch {result.head_branch!r} is the same as the base"
    if result.head_branch in PROTECTED or result.head_branch == default:
        return f"the head branch {result.head_branch!r} is protected ({', '.join(sorted({*PROTECTED, default}))})"
    if result.base_branch != default and result.base_branch != allow_base:
        return (f"the base is {result.base_branch!r}, not the default branch {default!r}; "
                f"pass --allow-base {result.base_branch} for a stacked PR")
    return None


def land(pr: str, timeout: float, interval: float, say=print, allow_base: str | None = None) -> int:
    # 1. read the status before anything goes outward
    result = ci_wait.wait(pr, timeout, interval)
    say(f"{result.sha[:7]} {result.status} {result.conclusion}  {result.merge_state}")
    if not result.ok:
        say(f"ship-land: refusing to merge PR {result.pr}: {result.reason}")
        return result.code or 1
    default = ci_wait.gh_json("repo", "view", "--json", "defaultBranchRef")["defaultBranchRef"]["name"]
    why = refusal(result, default, allow_base)
    if why:
        say(f"ship-land: refusing: {why}. Nothing was merged.")
        return 1
    pr_commits = ci_wait.gh_json("pr", "view", pr, "--json", "commits")
    base, branch = result.base_branch, result.head_branch
    trees = worktrees()
    branch_worktree = worktree_for(branch, trees)
    if branch_worktree and inside(branch_worktree, os.getcwd()):
        say(f"ship-land: refusing: this shell is inside the worktree {branch_worktree} that would be removed; "
            "run it from another checkout. Nothing was merged.")
        return 1
    # 2. merge, pinned to the head that was checked
    mode = "--rebase" if len(pr_commits["commits"]) == 1 else "--merge"
    merge_proc = run("gh", "pr", "merge", result.pr, mode, "--match-head-commit", result.sha)
    if merge_proc.returncode != 0:
        say(f"ship-land: merge failed, nothing deleted: {merge_proc.stderr.strip() or merge_proc.stdout.strip()}")
        return 1
    say(f"merged PR {result.pr} with {mode} at head {result.sha[:7]}")
    state = ci_wait.gh_json("pr", "view", pr, "--json", "state")["state"]
    if state != "MERGED":
        say(f"ship-land: gh reported success but the PR is {state}; nothing deleted")
        return 1
    rc = 0
    # 3. sync the base branch, fast-forward only
    base_worktree = worktree_for(base, trees)
    fetch_proc = run("git", "fetch", "origin", base)
    if fetch_proc.returncode == 0:
        if base_worktree:
            sync_proc = run("git", "merge", "--ff-only", "FETCH_HEAD", cwd=base_worktree)
        else:
            sync_proc = run("git", "fetch", "origin", f"{base}:{base}")
        ok = sync_proc.returncode == 0
        detail = sync_proc.stderr.strip() or sync_proc.stdout.strip()
    else:
        ok, detail = False, fetch_proc.stderr.strip()
    say(f"synced {base} (--ff-only)" if ok else f"sync of {base} FAILED: {detail}")
    rc |= 0 if ok else 1
    # 4. worktree, then local branch (the branch is checked out there until it goes)
    if branch_worktree:
        remove_proc = run("git", "worktree", "remove", branch_worktree)
        say(f"removed worktree {branch_worktree}" if remove_proc.returncode == 0
            else f"worktree removal FAILED: {remove_proc.stderr.strip()}")
        rc |= 0 if remove_proc.returncode == 0 else 1
    # -d first; a --rebase merge leaves the local commits "unmerged" to it, so -D only when the
    # local branch is exactly the head that was verified and merged
    branch_del_proc = run("git", "branch", "-d", branch)
    if branch_del_proc.returncode != 0:
        local_head = run("git", "rev-parse", f"refs/heads/{branch}")
        if local_head.returncode == 0 and local_head.stdout.strip() == result.sha:
            branch_del_proc = run("git", "branch", "-D", branch)
        else:
            say(f"left local branch {branch}: it is not the verified head {result.sha[:7]} "
                f"({branch_del_proc.stderr.strip()}); delete it by hand if it is not wanted")
            branch_del_proc = None
    if branch_del_proc is not None:
        say(f"deleted local branch {branch}" if branch_del_proc.returncode == 0
            else f"local branch delete FAILED: {branch_del_proc.stderr.strip()}")
        rc |= 0 if branch_del_proc.returncode == 0 else 1
    pr_list_proc = run("gh", "pr", "list", "--base", branch, "--state", "open", "--json", "number")
    try:
        dependent_prs = json.loads(pr_list_proc.stdout) if pr_list_proc.returncode == 0 else None
    except ValueError:
        dependent_prs = None
    if dependent_prs is None:
        say(f"remote branch {branch} NOT deleted: could not list open PRs based on it")
        rc |= 1
    elif dependent_prs:
        pr_numbers = ", ".join(f"#{dependent_pr.get('number')}" for dependent_pr in dependent_prs)
        say(f"remote branch {branch} NOT deleted: open PR {pr_numbers} is based on it; retarget it first")
    else:
        push_del_proc = run("git", "push", "origin", "--delete", branch)
        if push_del_proc.returncode == 0:
            say(f"deleted remote branch {branch}")
        elif "remote ref does not exist" in push_del_proc.stderr:
            say(f"remote branch {branch} already gone")
        else:
            say(f"remote branch delete FAILED: {push_del_proc.stderr.strip()}")
            rc |= 1
    say("closed no issues: close any the PR resolves by hand, and say so")
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("pr", help="PR number")
    parser.add_argument("--timeout", type=float, default=ci_wait.DEFAULT_TIMEOUT)
    parser.add_argument("--interval", type=float, default=ci_wait.DEFAULT_INTERVAL)
    parser.add_argument("--allow-base", metavar="BRANCH",
                    help="permit a PR whose base is BRANCH instead of the default branch "
                         "(experimental for per-task PRs, or a stacked PR's base)")
    args = parser.parse_args(argv)
    try:
        return land(args.pr, args.timeout, args.interval, allow_base=args.allow_base)
    except (ci_wait.GhError, OSError) as e:
        print(f"ship-land: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
