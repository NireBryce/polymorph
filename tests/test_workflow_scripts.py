import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from stubtools import Stubs, js, out

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SHA = "abcdef0123456789abcdef0123456789abcdef01"
NEW = "1111111222222233333334444444555555566666"


def pr(state="OPEN", merge="CLEAN", sha=SHA, branch="feat/x", n=7, base="main", cross=False):
    return {"number": n, "headRefOid": sha, "headRefName": branch, "baseRefName": base,
            "isCrossRepository": cross, "mergeStateStatus": merge, "state": state}


def runs(*pairs):
    return [{"status": s, "conclusion": c} for s, c in pairs]


PR_VIEW = ["gh", "pr", "view"]
RUN_LIST = ["gh", "run", "list"]


def script(name, args, stubs, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPTS / name), *args], capture_output=True, text=True,
                          env=stubs.env(), cwd=cwd)


class CiWaitTests(unittest.TestCase):
    def wait(self, scenario, *args):
        s = Stubs(scenario)
        r = script("ci_wait.py", ["7", "--interval", "0", *args], s)
        return r, s

    def test_success_and_clean_exits_0(self):
        r, _ = self.wait([{"match": PR_VIEW, "outputs": [js(pr())]},
                          {"match": RUN_LIST, "outputs": [js(runs(("completed", "success")))]}])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["abcdef0", "completed", "success", "CLEAN"])

    def test_failure_exits_1_and_says_so(self):
        r, _ = self.wait([{"match": PR_VIEW, "outputs": [js(pr(merge="UNSTABLE"))]},
                          {"match": RUN_LIST, "outputs": [js(runs(("completed", "failure")))]}])
        self.assertEqual(r.returncode, 1)
        self.assertIn("abcdef0 completed failure", r.stdout)

    def test_success_but_not_clean_exits_1(self):
        r, _ = self.wait([{"match": PR_VIEW, "outputs": [js(pr(merge="BEHIND"))]},
                          {"match": RUN_LIST, "outputs": [js(runs(("completed", "success")))]}])
        self.assertEqual(r.returncode, 1)
        self.assertIn("BEHIND", r.stdout)

    def test_one_failing_run_among_several_fails(self):
        r, _ = self.wait([{"match": PR_VIEW, "outputs": [js(pr())]},
                          {"match": RUN_LIST, "outputs": [js(runs(("completed", "success"), ("completed", "cancelled")))]}])
        self.assertEqual(r.returncode, 1)

    def test_waits_through_in_progress_then_succeeds(self):
        r, s = self.wait([{"match": PR_VIEW, "outputs": [js(pr(merge="UNKNOWN")), js(pr())]},
                          {"match": RUN_LIST, "outputs": [js([]), js(runs(("in_progress", None))),
                                                          js(runs(("completed", "success")))]}])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGreaterEqual(len([c for c in s.calls() if c[:3] == RUN_LIST]), 3)

    def test_unknown_merge_state_after_success_keeps_polling(self):
        r, _ = self.wait([{"match": PR_VIEW, "outputs": [js(pr(merge="UNKNOWN")), js(pr())]},
                          {"match": RUN_LIST, "outputs": [js(runs(("completed", "success")))]}])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_timeout_is_nonzero(self):
        r, _ = self.wait([{"match": PR_VIEW, "outputs": [js(pr())]},
                          {"match": RUN_LIST, "outputs": [js(runs(("in_progress", None)))]}],
                         "--timeout", "0.2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("timed out", r.stderr)

    def test_gh_failure_is_nonzero(self):
        r, _ = self.wait([{"match": PR_VIEW, "outputs": [out(1, err="no pull request found")]}])
        self.assertEqual(r.returncode, 2)
        self.assertIn("no pull request", r.stderr)

    def test_closed_pr_fails(self):
        r, _ = self.wait([{"match": PR_VIEW, "outputs": [js(pr(state="MERGED"))]},
                          {"match": RUN_LIST, "outputs": [js(runs(("completed", "success")))]}])
        self.assertEqual(r.returncode, 1)

    def test_follows_a_new_head_commit(self):
        r, s = self.wait([{"match": PR_VIEW, "outputs": [js(pr(sha=SHA, merge="BLOCKED")), js(pr(sha=NEW))]},
                          {"match": RUN_LIST + ["--commit", SHA], "outputs": [js(runs(("in_progress", None)))]},
                          {"match": RUN_LIST + ["--commit", NEW], "outputs": [js(runs(("completed", "success")))]}])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("1111111"))

    def test_default_target_is_current_branch(self):
        s = Stubs([{"match": ["git", "branch"], "outputs": [out(out="feat/x\n")]},
                   {"match": PR_VIEW, "outputs": [js(pr())]},
                   {"match": RUN_LIST, "outputs": [js(runs(("completed", "success")))]}])
        r = script("ci_wait.py", ["--interval", "0"], s)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(["gh", "pr", "view", "feat/x"], [c[:4] for c in s.calls()])


def land_scenario(merge_out=None, trees="", merge_state="CLEAN", commits=2, delete=None, final="MERGED",
                  branch="feat/x", base="main", cross=False, default="main", branch_d=None,
                  local_sha=SHA, other_prs=()):
    return [
        {"match": ["gh", "pr", "view", "7", "--json", "commits"],
         "outputs": [js({"commits": [{}] * commits})]},
        {"match": ["gh", "pr", "view", "7", "--json", "state"], "outputs": [js({"state": final})]},
        {"match": PR_VIEW, "outputs": [js(pr(merge=merge_state, branch=branch, base=base, cross=cross))]},
        {"match": ["gh", "repo", "view"], "outputs": [js({"defaultBranchRef": {"name": default}})]},
        {"match": ["gh", "pr", "list"], "outputs": [js([{"number": n} for n in other_prs])]},
        {"match": ["git", "branch", "-d"], "outputs": [branch_d or out()]},
        {"match": ["git", "rev-parse"], "outputs": [out(out=local_sha + "\n")]},
        {"match": RUN_LIST, "outputs": [js(runs(("completed", "success")))]},
        {"match": ["gh", "pr", "merge"], "outputs": [merge_out or out()]},
        {"match": ["git", "worktree", "list"], "outputs": [out(out=trees)]},
        {"match": ["git", "push", "origin", "--delete"], "outputs": [delete or out()]},
        {"match": ["git"], "outputs": [out()]},
    ]


_ROOT = tempfile.mkdtemp(prefix="pm-trees-")   # the base checkout must exist: git is run inside it
os.mkdir(f"{_ROOT}/repo")
os.mkdir(f"{_ROOT}/repo-wt")
TREES = (f"worktree {_ROOT}/repo\nHEAD aaa\nbranch refs/heads/main\n\n"
         f"worktree {_ROOT}/repo-wt\nHEAD bbb\nbranch refs/heads/feat/x\n")


class ShipLandTests(unittest.TestCase):
    def land(self, scenario, *args):
        s = Stubs(scenario)
        r = script("ship_land.py", ["7", "--interval", "0", "--timeout", "1", *args], s, cwd="/")
        return r, s.calls()

    def gh_merge(self, calls):
        return [c for c in calls if c[:3] == ["gh", "pr", "merge"]]

    def test_merge_pinned_to_head_and_cleans_up(self):
        r, calls = self.land(land_scenario(trees=TREES))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.gh_merge(calls), [["gh", "pr", "merge", "7", "--merge", "--match-head-commit", SHA]])
        self.assertIn(["git", "merge", "--ff-only", "FETCH_HEAD"], calls)
        self.assertIn(["git", "worktree", "remove", f"{_ROOT}/repo-wt"], calls)
        self.assertIn(["git", "branch", "-d", "feat/x"], calls)
        self.assertNotIn(["git", "branch", "-D", "feat/x"], calls)
        self.assertIn(["git", "push", "origin", "--delete", "feat/x"], calls)
        self.assertNotIn("--delete-branch", [a for c in calls for a in c])
        self.assertIn("closed no issues", r.stdout)

    def test_single_commit_uses_rebase(self):
        r, calls = self.land(land_scenario(commits=1))
        self.assertEqual(self.gh_merge(calls)[0][4], "--rebase")

    def test_no_base_worktree_fetches_ref_forward_only(self):
        r, calls = self.land(land_scenario())
        self.assertIn(["git", "fetch", "origin", "main:main"], calls)
        self.assertNotIn(["git", "worktree", "remove", "x"], calls)

    def test_refuses_when_ci_not_clean(self):
        r, calls = self.land(land_scenario(merge_state="BEHIND"))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.gh_merge(calls), [])
        self.assertNotIn(["git", "push", "origin", "--delete", "feat/x"], calls)

    def test_failed_merge_deletes_nothing(self):
        r, calls = self.land(land_scenario(merge_out=out(1, err="head branch was modified")))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual([c for c in calls if c[:2] in (["git", "branch"], ["git", "push"])], [])

    def test_remote_already_gone_counts_as_done(self):
        r, _ = self.land(land_scenario(delete=out(1, err="error: unable to delete 'feat/x': remote ref does not exist")))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("already gone", r.stdout)

    def test_other_remote_delete_failure_is_reported(self):
        r, _ = self.land(land_scenario(delete=out(1, err="permission denied")))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FAILED", r.stdout)

    def test_pr_not_merged_after_gh_success_deletes_nothing(self):
        r, calls = self.land(land_scenario(final="OPEN"))
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn(["git", "branch", "-D", "feat/x"], calls)

    def test_refuses_from_inside_the_worktree_before_merging(self):
        s = Stubs(land_scenario(trees=TREES.replace(f"{_ROOT}/repo-wt", "/")))
        r = script("ship_land.py", ["7", "--interval", "0", "--timeout", "1"], s, cwd="/")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.gh_merge(s.calls()), [])

    # -- refusals that happen before anything goes outward --------------------------------
    def refuses_early(self, scenario, *args, text=""):
        r, calls = self.land(scenario, *args)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(self.gh_merge(calls), [])
        self.assertEqual([c for c in calls if c[:2] in (["git", "branch"], ["git", "push"])], [])
        self.assertIn(text, r.stdout)

    def test_fork_pr_named_main_is_refused_before_merging(self):
        self.refuses_early(land_scenario(branch="main", cross=True, trees=TREES), text="another repository")

    def test_cross_repository_pr_is_refused(self):
        self.refuses_early(land_scenario(cross=True), text="another repository")

    def test_head_equal_to_base_is_refused(self):
        self.refuses_early(land_scenario(branch="main", base="main"), text="same as the base")

    def test_protected_head_branches_are_refused(self):
        for name in ("main", "master", "experimental"):
            with self.subTest(name=name):
                self.refuses_early(land_scenario(branch=name, base="other", default="trunk"),
                                   "--allow-base", "other", text="protected")

    def test_repo_default_branch_is_protected(self):
        self.refuses_early(land_scenario(branch="trunk", base="main", default="trunk"),
                           "--allow-base", "main", text="protected")

    def test_base_that_is_not_the_default_is_refused(self):
        self.refuses_early(land_scenario(base="stack-1"), text="--allow-base")

    def test_allow_base_permits_a_stacked_pr(self):
        r, calls = self.land(land_scenario(base="stack-1"), "--allow-base", "stack-1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(len(self.gh_merge(calls)), 1)

    # -- local and remote branch deletion --------------------------------------------------
    def test_rebase_merge_deletes_local_branch_when_it_is_the_verified_head(self):
        r, calls = self.land(land_scenario(branch_d=out(1, err="not fully merged")))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(["git", "rev-parse", "refs/heads/feat/x"], calls)
        self.assertIn(["git", "branch", "-D", "feat/x"], calls)

    def test_local_branch_with_other_commits_is_left_alone(self):
        r, calls = self.land(land_scenario(branch_d=out(1, err="not fully merged"), local_sha=NEW))
        self.assertNotIn(["git", "branch", "-D", "feat/x"], calls)
        self.assertIn("left", r.stdout)

    def test_remote_branch_with_a_dependent_open_pr_is_not_deleted(self):
        r, calls = self.land(land_scenario(other_prs=[9]))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(["gh", "pr", "list", "--base", "feat/x", "--state", "open", "--json", "number"], calls)
        self.assertNotIn(["git", "push", "origin", "--delete", "feat/x"], calls)
        self.assertIn("open PR", r.stdout)


if __name__ == "__main__":
    unittest.main()
