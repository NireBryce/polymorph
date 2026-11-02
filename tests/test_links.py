"""Tests for link creation (issue #39): equip and adopt close `no-link` gaps and nothing else."""
from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from polymorph import adopt, cli, equip, inventory, links
from polymorph.resources import COVERAGE_DIR

from test_adopt import rest_repo
from test_equip import equip_repo


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class TmpRepo(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = equip_repo(self.root / "repo")

    def readlink(self, rel):
        return os.readlink(self.repo / rel)

    def gap_reasons(self, harness):
        cap = inventory.load_coverage(harness, self.repo)
        inv = inventory.read_inventory(self.repo)
        return sorted((g.path, g.reason) for g in inventory.gaps(inv, cap, self.repo))


class EquipLinks(TmpRepo):
    def test_equip_claude_links_what_claude_reads(self):
        self.assertIn(("AGENTS.md", "no-link"), self.gap_reasons("claude"))
        r = equip.equip(self.repo, "claude")
        self.assertEqual(self.readlink("CLAUDE.md"), "AGENTS.md")
        self.assertEqual(self.readlink(".claude/skills"), "../.agents/skills")
        self.assertEqual({(l.path, l.target) for l in r.linked},
                         {("CLAUDE.md", "AGENTS.md"), (".claude/skills", "../.agents/skills")})
        self.assertEqual(self.gap_reasons("claude"), [])
        self.assertEqual(r.gaps, [])

    def test_the_links_resolve_to_the_provided_files(self):
        equip.equip(self.repo, "claude")
        self.assertEqual((self.repo / "CLAUDE.md").read_text(), "instructions\n")
        self.assertTrue((self.repo / ".claude/skills/alpha/SKILL.md").is_file())

    def test_equip_zcode_makes_no_links(self):
        r = equip.equip(self.repo, "zcode")
        self.assertEqual(r.linked, [])
        self.assertFalse((self.repo / "CLAUDE.md").exists())
        self.assertFalse((self.repo / ".claude").exists())

    def test_links_are_recorded_and_tell_ours_from_the_users(self):
        equip.equip(self.repo, "claude")
        os.symlink("AGENTS.md", self.repo / "USERS.md")
        got = {(l.harness, l.path, l.target) for l in equip.manifest_links(self.repo)}
        self.assertEqual(got, {("claude", "CLAUDE.md", "AGENTS.md"),
                               ("claude", ".claude/skills", "../.agents/skills")})
        text = (self.repo / equip.MANIFEST).read_text()
        self.assertIn("[links.claude]", text)
        self.assertNotIn("USERS.md", text)

    def test_a_second_run_changes_nothing(self):
        equip.equip(self.repo, "claude")
        before = (self.repo / equip.MANIFEST).read_text()
        r = equip.equip(self.repo, "claude")
        self.assertEqual(r.linked, [])
        self.assertEqual((self.repo / equip.MANIFEST).read_text(), before)

    def test_equipping_another_harness_keeps_the_links_record(self):
        equip.equip(self.repo, "claude")
        equip.equip(self.repo, "zcode")
        self.assertEqual(len(equip.manifest_links(self.repo, "claude")), 2)
        self.assertTrue(equip.manifest_entries(self.repo, "zcode"))

    def test_dry_run_lists_and_writes_nothing(self):
        r = equip.equip(self.repo, "claude", dry_run=True)
        self.assertEqual(len(r.linked), 2)
        self.assertFalse((self.repo / "CLAUDE.md").exists(), "dry run made a link")
        self.assertFalse((self.repo / ".claude").exists())
        self.assertFalse((self.repo / equip.MANIFEST).exists())

    def test_no_links_flag_reports_the_gaps_only(self):
        r = equip.equip(self.repo, "claude", make_links=False)
        self.assertEqual(r.linked, [])
        self.assertFalse((self.repo / "CLAUDE.md").exists())
        self.assertTrue(any(g.reason == "no-link" for g in r.gaps))

    def test_a_real_copy_is_not_replaced(self):
        (self.repo / "CLAUDE.md").write_text("a separate copy\n")
        r = equip.equip(self.repo, "claude")
        self.assertFalse((self.repo / "CLAUDE.md").is_symlink())
        self.assertEqual((self.repo / "CLAUDE.md").read_text(), "a separate copy\n")
        self.assertNotIn("CLAUDE.md", [l.path for l in r.linked])
        self.assertIn("unlinked-copy", [g.reason for g in r.gaps])

    def test_a_symlink_to_elsewhere_is_not_rewritten(self):
        (self.repo / "other.md").write_text("x\n")
        os.symlink("other.md", self.repo / "CLAUDE.md")
        r = equip.equip(self.repo, "claude")
        self.assertEqual(self.readlink("CLAUDE.md"), "other.md")
        self.assertNotIn("CLAUDE.md", [l.path for l in r.linked])
        self.assertIn("points-elsewhere", [g.reason for g in r.gaps])

    def test_a_repo_local_coverage_table_creates_no_links(self):
        d = self.repo / ".polymorph/basecamp/coverage"
        d.mkdir(parents=True)
        (d / "claude.toml").write_text(
            'schema = 1\nname = "claude"\n[inventory]\nskills = "build/out"\nmemory = ["CLAUDE.md"]\n')
        r = equip.equip(self.repo, "claude")
        self.assertEqual(r.linked, [])
        self.assertFalse((self.repo / "build").exists())
        self.assertFalse((self.repo / "CLAUDE.md").exists())
        self.assertTrue(r.link_refused)
        self.assertIn("repo-local", r.link_refused[0][2])

    def test_a_parent_symlink_that_leaves_the_repo_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        os.symlink(outside, self.repo / ".claude")
        r = equip.equip(self.repo, "claude")
        self.assertEqual([p.name for p in outside.iterdir() if p.is_symlink()], [],
                         "a link was made outside the repository")
        self.assertIn(".claude/skills", [p for _, p, _ in r.link_refused])

    def test_the_cli_prints_the_links_and_exits_0(self):
        code, out, _ = run_cli("equip", "--repo", str(self.repo), "--harness", "claude")
        self.assertEqual(code, 0)
        self.assertIn("[claude] linked CLAUDE.md -> AGENTS.md", out)
        self.assertIn("[claude] linked .claude/skills -> ../.agents/skills", out)
        self.assertIn("gaps (will be silently ignored): 0", out)

    def test_the_cli_dry_run_says_would_link(self):
        code, out, _ = run_cli("equip", "--repo", str(self.repo), "--harness", "claude", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would link CLAUDE.md -> AGENTS.md", out)
        self.assertFalse((self.repo / "CLAUDE.md").exists())

    def test_the_cli_no_links_flag(self):
        code, out, _ = run_cli("equip", "--repo", str(self.repo), "--harness", "claude", "--no-links")
        self.assertEqual(code, 0)
        self.assertNotIn("linked", out.replace("unlinked", ""))
        self.assertIn("polymorph equip --harness claude links CLAUDE.md -> AGENTS.md", out)


class PlanTests(unittest.TestCase):
    def cap(self, name, **kw):
        return inventory.Coverage(name=name, source=str(COVERAGE_DIR / f"{name}.toml"), **kw)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()
        (self.repo / ".agents/skills/a").mkdir(parents=True)
        (self.repo / ".agents/skills/a/SKILL.md").write_text("s\n")
        (self.repo / "AGENTS.md").write_text("m\n")

    def test_git_and_polymorph_are_never_link_locations(self):
        for bad in (".git/hooks", ".polymorph/x"):
            with self.subTest(bad):
                p = links.plan(self.repo, [self.cap("x", skills=bad)], inventory.read_inventory(self.repo))
                self.assertEqual(p.create, [])
                self.assertEqual(len(p.refused), 1)

    def test_one_link_per_directory_however_many_items(self):
        for n in ("b", "c"):
            (self.repo / ".agents/skills" / n).mkdir()
            (self.repo / ".agents/skills" / n / "SKILL.md").write_text("s\n")
        p = links.plan(self.repo, [self.cap("x", skills=".x/skills")], inventory.read_inventory(self.repo))
        self.assertEqual([(l.path, l.target) for l in p.create], [(".x/skills", "../.agents/skills")])

    def test_two_harnesses_needing_the_same_link_make_it_once_and_both_record_it(self):
        caps = [self.cap("x", skills=".s/skills"), self.cap("y", skills=".s/skills")]
        p = links.plan(self.repo, caps, inventory.read_inventory(self.repo))
        made, failed = links.apply(self.repo, p.create)
        self.assertEqual(failed, [])
        self.assertEqual(sorted(l.harness for l in made), ["x", "y"])
        self.assertEqual(os.readlink(self.repo / ".s/skills"), "../.agents/skills")

    def test_the_same_path_with_different_targets_is_refused(self):
        (self.repo / "docs").mkdir()
        (self.repo / "docs/note.md").write_text("n\n")
        caps = [self.cap("x", memory=("OUT.md",)), self.cap("y", memory=("OUT.md",))]
        inv = inventory.Inventory(memory=(inventory.Item("memory", "AGENTS.md"),))
        self.assertEqual(len(links.plan(self.repo, caps, inv).create), 2)   # same target: fine
        inv2 = inventory.Inventory(memory=(inventory.Item("memory", "AGENTS.md"),
                                           inventory.Item("memory", "docs/note.md")))
        p = links.plan(self.repo, [self.cap("x", memory=("OUT.md",))], inv2)
        self.assertEqual(p.create, [])
        self.assertEqual(len(p.refused), 2)

    def test_apply_never_replaces_a_path_that_appeared(self):
        (self.repo / ".x").mkdir()
        (self.repo / ".x/skills").write_text("mine\n")
        made, failed = links.apply(self.repo, [links.Link("x", ".x/skills", "../.agents/skills")])
        self.assertEqual(made, [])
        self.assertEqual((self.repo / ".x/skills").read_text(), "mine\n")
        self.assertEqual(len(failed), 1)


class AdoptLinks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / "repo").mkdir()
        self.repo = rest_repo(self.root / "repo")

    def test_adopt_links_what_claude_needs_after_the_move(self):
        r = adopt.adopt(self.repo)
        self.assertEqual(os.readlink(self.repo / ".claude/skills"), "../.agents/skills")
        self.assertTrue((self.repo / ".claude/skills/alpha/SKILL.md").is_file())
        self.assertEqual([(l.harness, l.path) for l in r.linked], [("claude", ".claude/skills")])
        self.assertEqual([(l.path, l.target) for l in equip.manifest_links(self.repo)],
                         [(".claude/skills", "../.agents/skills")])

    def test_adopt_leaves_the_two_memory_copies_alone(self):
        r = adopt.adopt(self.repo)
        self.assertNotIn("CLAUDE.md", [l.path for l in r.linked])
        self.assertTrue((self.repo / "CLAUDE.md").is_symlink())
        self.assertEqual(os.readlink(self.repo / "CLAUDE.md"), ".polymorph/basecamp/CLAUDE.md")

    def test_adopt_dry_run_lists_the_links_and_makes_none(self):
        r = adopt.adopt(self.repo, dry_run=True)
        self.assertEqual([l.path for l in r.linked], [".claude/skills"])
        self.assertFalse((self.repo / ".claude/skills").exists())
        self.assertFalse((self.repo / equip.MANIFEST).exists())

    def test_adopt_no_links(self):
        r = adopt.adopt(self.repo, make_links=False)
        self.assertEqual(r.linked, [])
        self.assertFalse((self.repo / ".claude/skills").exists())

    def test_the_cli_prints_links_and_counts_them(self):
        code, out, _ = run_cli("manage", "adopt", "--repo", str(self.repo))
        self.assertEqual(code, 0, out)
        self.assertIn("linked [claude] .claude/skills -> ../.agents/skills", out)
        self.assertIn("1 linked", out)

    def test_a_refused_link_fails_the_run(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / ".claude").rename(self.repo / ".claude-real")
        os.symlink(outside, self.repo / ".claude")
        code, out, _ = run_cli("manage", "adopt", "--repo", str(self.repo), "--dry-run")
        self.assertEqual(code, 1, out)
        self.assertIn("link refused [claude] .claude/skills", out)


if __name__ == "__main__":
    unittest.main()
