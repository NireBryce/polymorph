"""Tests for adopt: moving agent files at rest into the basecamp store."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from polymorph import adopt


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def rest_repo(root: Path, git_init: bool = True) -> Path:
    """A repo with agent files at rest, the shape adopt exists to convert."""
    (root / ".agents/skills/alpha").mkdir(parents=True)
    (root / ".agents/skills/alpha/SKILL.md").write_text("skill\n")
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/keep.md").write_text("rule\n")
    (root / "AGENTS.md").write_text("instructions\n")
    (root / "CLAUDE.md").write_text("instructions\n")
    (root / ".agents/hooks").mkdir(parents=True)
    (root / ".agents/hooks/g.sh").write_text("#!/bin/sh\n")
    if git_init:
        git(root, "init", "-q")
        git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        git(root, "-c", "commit.gpgsign=false", "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-qm", "rest")
    return root


class AdoptTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = rest_repo(Path(tmp.name))

    def test_adopt_moves_into_the_store_and_symlinks_back(self):
        r = adopt.adopt(self.repo)
        self.assertTrue(r.ok())
        self.assertEqual([(a.kind, a.path) for a in r.moved],
                         [("memory", "AGENTS.md"), ("memory", "CLAUDE.md"),
                          ("skills", ".agents/skills"), ("rules", ".claude/rules")])
        self.assertEqual([a for a in r.absent], [("rules", ".agents/rules")])
        for at_rest, store in (("AGENTS.md", ".polymorph/basecamp/AGENTS.md"),
                               ("CLAUDE.md", ".polymorph/basecamp/CLAUDE.md")):
            self.assertTrue((self.repo / store).is_file())
            self.assertTrue((self.repo / at_rest).is_symlink())
            self.assertEqual(os.path.realpath(self.repo / at_rest),
                             os.path.realpath(self.repo / store))
        self.assertTrue((self.repo / ".polymorph/basecamp/skills/alpha/SKILL.md").is_file())
        self.assertTrue((self.repo / ".agents/skills").is_symlink())
        self.assertTrue((self.repo / ".polymorph/basecamp/rules/claude/keep.md").is_file())
        self.assertTrue((self.repo / ".claude/rules").is_symlink())
        # links are relative
        self.assertEqual(os.readlink(self.repo / "AGENTS.md"), ".polymorph/basecamp/AGENTS.md")
        self.assertEqual(os.readlink(self.repo / ".agents/skills"),
                         "../.polymorph/basecamp/skills")

    def test_adopt_never_touches_hooks_or_settings(self):
        adopt.adopt(self.repo)
        self.assertTrue((self.repo / ".agents/hooks/g.sh").is_file())
        self.assertFalse((self.repo / ".agents/hooks").is_symlink())

    def test_second_run_is_a_noop(self):
        adopt.adopt(self.repo)
        store_before = sorted(p.relative_to(self.repo).as_posix()
                              for p in (self.repo / ".polymorph").rglob("*") if p.is_file())
        r = adopt.adopt(self.repo)
        self.assertEqual(r.moved, [])
        self.assertEqual({(k, p) for k, p, _ in r.already},
                         {("memory", "AGENTS.md"), ("memory", "CLAUDE.md"),
                          ("skills", ".agents/skills"), ("rules", ".claude/rules")})
        store_after = sorted(p.relative_to(self.repo).as_posix()
                             for p in (self.repo / ".polymorph").rglob("*") if p.is_file())
        self.assertEqual(store_before, store_after)

    def test_dry_run_writes_nothing(self):
        r = adopt.adopt(self.repo, dry_run=True)
        self.assertEqual(len(r.moved), 4)
        self.assertTrue((self.repo / "AGENTS.md").is_file())
        self.assertFalse((self.repo / ".polymorph/basecamp/AGENTS.md").exists())

    def test_dirty_file_is_refused_others_still_move(self):
        (self.repo / "AGENTS.md").write_text("changed\n")
        r = adopt.adopt(self.repo)
        self.assertFalse(r.ok())
        self.assertEqual([(k, p) for k, p, _ in r.refused], [("memory", "AGENTS.md")])
        self.assertEqual([(a.kind, a.path) for a in r.moved],
                         [("memory", "CLAUDE.md"), ("skills", ".agents/skills"),
                          ("rules", ".claude/rules")])
        self.assertTrue((self.repo / "AGENTS.md").is_file())  # left as it was
        self.assertEqual((self.repo / "AGENTS.md").read_text(), "changed\n")

    def test_no_git_means_no_moves(self):
        shutil.rmtree(self.repo / ".git")
        r = adopt.adopt(self.repo)
        self.assertFalse(r.ok())
        self.assertEqual(r.moved, [])
        self.assertEqual(len(r.refused), 4)
        self.assertTrue(all("git status failed" in reason for _, _, reason in r.refused))
        self.assertTrue((self.repo / "AGENTS.md").is_file())

    def test_pre_symlinked_path_is_left_alone(self):
        store = self.repo / ".polymorph/basecamp"
        store.mkdir(parents=True)
        (store / "AGENTS.md").write_text("canonical\n")
        (self.repo / "AGENTS.md").unlink()
        (self.repo / "AGENTS.md").symlink_to(".polymorph/basecamp/AGENTS.md")
        r = adopt.adopt(self.repo)
        self.assertTrue(r.ok())
        self.assertIn(("memory", "AGENTS.md"), [(k, p) for k, p, _ in r.already])
        self.assertEqual((self.repo / ".polymorph/basecamp/AGENTS.md").read_text(), "canonical\n")

    def test_both_copies_exist_is_refused(self):
        store = self.repo / ".polymorph/basecamp"
        store.mkdir(parents=True)
        (store / "AGENTS.md").write_text("canonical\n")  # store copy without the link
        r = adopt.adopt(self.repo)
        self.assertFalse(r.ok())
        self.assertIn("both copies exist", dict(((k, p), reason) for k, p, reason in r.refused)[("memory", "AGENTS.md")])


class AdoptCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = rest_repo(Path(tmp.name))

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        import contextlib
        import io

        from polymorph import cli

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_cli_adopt_exit_0_and_table(self):
        rc, out, err = self.run_cli("adopt", "--repo", str(self.repo))
        self.assertEqual(rc, 0, (out, err))
        self.assertIn("moved [memory] AGENTS.md -> .polymorph/basecamp/AGENTS.md", out)
        self.assertIn("4 moved, 0 already adopted, 1 absent, 0 refused", out)

    def test_cli_adopt_refusal_exits_1(self):
        (self.repo / "AGENTS.md").write_text("changed\n")
        rc, out, _ = self.run_cli("adopt", "--repo", str(self.repo))
        self.assertEqual(rc, 1)
        self.assertIn("refused [memory] AGENTS.md: uncommitted changes", out)


if __name__ == "__main__":
    unittest.main()
