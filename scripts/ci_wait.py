#!/usr/bin/env python3
"""Wait for CI on a PR's current head commit.

    python3 scripts/ci_wait.py [PR-or-branch] [--timeout SECONDS] [--interval SECONDS]
    just ci-wait [PR-or-branch]

Prints `<sha7> <status> <conclusion>` for the workflow runs on the head commit,
then the PR's mergeStateStatus. Exit 0 only when every run on that commit
succeeded and the PR is CLEAN; 1 for a failed run or a PR that is not CLEAN;
2 on timeout or when gh cannot answer. The head commit is read again on every
poll, so a push during the wait is followed, not ignored.

Needs `gh` on PATH, run inside the repository (or a worktree of it).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass

DEFAULT_TIMEOUT = 900.0
DEFAULT_INTERVAL = 15.0


class GhError(Exception):
    pass


def gh_json(*args: str):
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise GhError(f"gh {' '.join(args[:2])} failed: {p.stderr.strip() or p.stdout.strip()}")
    try:
        return json.loads(p.stdout)
    except ValueError as e:
        raise GhError(f"gh {' '.join(args[:2])}: not JSON ({e})") from e


def current_branch() -> str:
    p = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True)
    name = p.stdout.strip()
    if p.returncode != 0 or not name:
        raise GhError("no PR or branch given and the current branch is detached or unknown")
    return name


def aggregate(runs: list[dict]) -> tuple[str, str]:
    """Collapse the runs on one commit to (status, conclusion)."""
    if not runs:
        return "none", "-"
    if any(r.get("status") != "completed" for r in runs):
        return "in_progress", "-"
    bad = [r.get("conclusion") or "unknown" for r in runs if r.get("conclusion") != "success"]
    return "completed", (bad[0] if bad else "success")


@dataclass
class Result:
    sha: str
    status: str
    conclusion: str
    merge_state: str
    pr: str
    head_branch: str
    ok: bool
    code: int
    reason: str

    def lines(self) -> str:
        return f"{self.sha[:7]} {self.status} {self.conclusion}\n{self.merge_state}\n"


def snapshot(target: str) -> tuple[dict, str, str]:
    pr = gh_json("pr", "view", target, "--json", "number,headRefOid,headRefName,mergeStateStatus,state")
    runs = gh_json("run", "list", "--commit", pr["headRefOid"], "--json", "status,conclusion")
    status, conclusion = aggregate(runs)
    return pr, status, conclusion


def wait(target: str | None = None, timeout: float = DEFAULT_TIMEOUT,
         interval: float = DEFAULT_INTERVAL, sleep=time.sleep, clock=time.monotonic) -> Result:
    target = target or current_branch()
    deadline = clock() + timeout
    while True:
        pr, status, conclusion = snapshot(target)
        merge = pr.get("mergeStateStatus") or "UNKNOWN"
        base = dict(sha=pr["headRefOid"], status=status, conclusion=conclusion, merge_state=merge,
                    pr=str(pr["number"]), head_branch=pr["headRefName"])
        if pr.get("state") != "OPEN":
            return Result(**base, ok=False, code=1, reason=f"PR is {pr.get('state')}, not OPEN")
        if status == "completed" and conclusion != "success":
            return Result(**base, ok=False, code=1, reason=f"CI concluded {conclusion}")
        # a finished run can still leave mergeStateStatus UNKNOWN for a moment: keep polling
        if status == "completed" and merge not in ("UNKNOWN", "UNSTABLE", "BLOCKED"):
            if merge == "CLEAN":
                return Result(**base, ok=True, code=0, reason="")
            return Result(**base, ok=False, code=1, reason=f"mergeStateStatus is {merge}, not CLEAN")
        if clock() >= deadline:
            return Result(**base, ok=False, code=2,
                          reason=f"timed out after {timeout:g}s (status {status}, merge state {merge})")
        print(f"{pr['headRefOid'][:7]} {status} {conclusion} {merge}: waiting", file=sys.stderr)
        sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", nargs="?", help="PR number or branch (default: the current branch)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds (default 900)")
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="seconds between polls")
    a = ap.parse_args(argv)
    try:
        r = wait(a.target, a.timeout, a.interval)
    except (GhError, OSError) as e:
        print(f"ci-wait: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(r.lines())
    if not r.ok:
        print(f"ci-wait: not ready: {r.reason}", file=sys.stderr)
    return r.code


if __name__ == "__main__":
    sys.exit(main())
