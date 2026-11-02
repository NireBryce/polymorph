"""Tests for the discover-and-report inventory and the per-harness gap report."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from polymorph import inventory


def full_repo(root: Path) -> Path:
    """A repo providing every discover-and-report kind, at rest."""
    (root / ".agents/skills/alpha").mkdir(parents=True)
    (root / ".agents/skills/alpha/SKILL.md").write_text("skill\n")
    (root / ".agents/skills/beta").mkdir(parents=True)  # no SKILL.md: reported, never dropped
    (root / ".agents/rules").mkdir(parents=True)
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/keep.md").write_text("rule\n")
    (root / ".agents/rules/extra.md").write_text("rule\n")
    (root / "AGENTS.md").write_text("instructions\n")
    (root / "CLAUDE.md").write_text("instructions\n")
    (root / ".claude/settings.json").write_text("{}\n")
    return root


class TempRepoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = full_repo(Path(tmp.name))
        self.inv = inventory.read_inventory(self.repo)


class InventoryTests(TempRepoTest):
    def test_every_kind_is_found(self):
        self.assertEqual([i.path for i in self.inv.skills],
                         [".agents/skills/alpha", ".agents/skills/beta"])
        self.assertEqual([i.path for i in self.inv.rules],
                         [".claude/rules/keep.md", ".agents/rules/extra.md"])
        self.assertEqual([i.path for i in self.inv.memory], ["AGENTS.md", "CLAUDE.md"])
        self.assertEqual([i.path for i in self.inv.claude_dir],
                         [".claude/rules", ".claude/settings.json"])

    def test_at_rest_items_have_no_target(self):
        self.assertTrue(all(i.target is None for i in self.inv.skills))

    def test_symlink_target_is_recorded(self):
        store = self.repo / ".polymorph/basecamp"
        store.mkdir(parents=True)
        (store / "AGENTS.md").write_text("instructions\n")
        (self.repo / "AGENTS.md").unlink()
        (self.repo / "AGENTS.md").symlink_to(".polymorph/basecamp/AGENTS.md")
        inv = inventory.read_inventory(self.repo)
        self.assertEqual(inv.memory[0].target, ".polymorph/basecamp/AGENTS.md")

    def test_absent_kinds_are_empty(self):
        (self.repo / "AGENTS.md").unlink()
        (self.repo / "CLAUDE.md").unlink()
        shutil.rmtree(self.repo / ".agents/skills")
        inv = inventory.read_inventory(self.repo)
        self.assertEqual(inv.skills, ())
        self.assertEqual(inv.memory, ())


class GapTests(TempRepoTest):
    def gaps_for(self, name):
        return [(g.kind, g.path, g.reason) for g in
                inventory.gaps(self.inv, inventory.load_coverage(name, self.repo), self.repo)]

    def test_phase1_gap_names_exactly_the_ignored_kind(self):
        """A coverage table that reads no rules: the gap report names exactly the rules."""
        table = self.repo / ".polymorph/basecamp/coverage"
        table.mkdir(parents=True)
        (table / "picker.toml").write_text(
            'schema = 1\nname = "picker"\n\n[inventory]\n'
            'skills = ".agents/skills"\nmemory = ["AGENTS.md", "CLAUDE.md"]\n'
            'claude_dir = ".claude"\n')
        self.assertEqual(self.gaps_for("picker"),
                         [("rules", ".claude/rules/keep.md", "unsupported"),
                          ("rules", ".agents/rules/extra.md", "unsupported")])

    def test_claude_on_a_repo_that_keeps_everything_under_agents_and_does_not_link(self):
        """AGENTS.md and .agents/* exist, nothing is linked for Claude Code: all reported."""
        (self.repo / "CLAUDE.md").unlink()
        shutil.rmtree(self.repo / ".claude/rules")
        self.inv = inventory.read_inventory(self.repo)
        got = self.gaps_for("claude")
        self.assertIn(("memory", "AGENTS.md", "no-link"), got)
        self.assertIn(("skills", ".agents/skills/alpha", "no-link"), got)
        self.assertIn(("rules", ".agents/rules/extra.md", "no-link"), got)

    def test_claude_with_both_copies_present_and_unlinked_is_drift(self):
        got = self.gaps_for("claude")
        self.assertIn(("memory", "AGENTS.md", "unlinked-copy"), got)      # CLAUDE.md is a real second file
        self.assertIn(("rules", ".agents/rules/extra.md", "unlinked-copy"), got)
        self.assertIn(("skills", ".agents/skills/alpha", "no-link"), got)  # .claude/skills is absent
        self.assertNotIn("CLAUDE.md", [p for _, p, _ in got])

    def test_symlinks_make_claude_reach_everything(self):
        (self.repo / "CLAUDE.md").unlink()
        (self.repo / "CLAUDE.md").symlink_to("AGENTS.md")
        shutil.rmtree(self.repo / ".claude/rules")
        (self.repo / ".claude/rules").symlink_to("../.agents/rules")
        (self.repo / ".claude/skills").symlink_to("../.agents/skills")
        self.inv = inventory.read_inventory(self.repo)
        self.assertEqual(self.gaps_for("claude"), [])

    def test_a_symlink_to_something_else_is_named(self):
        (self.repo / "CLAUDE.md").unlink()
        (self.repo / "other.md").write_text("x\n")
        (self.repo / "CLAUDE.md").symlink_to("other.md")
        self.inv = inventory.read_inventory(self.repo)
        self.assertIn(("memory", "AGENTS.md", "points-elsewhere"), self.gaps_for("claude"))

    def test_fix_text_names_the_link(self):
        (self.repo / "CLAUDE.md").unlink()
        self.inv = inventory.read_inventory(self.repo)
        g = next(g for g in inventory.gaps(self.inv, inventory.load_coverage("claude", self.repo), self.repo)
                 if g.path == "AGENTS.md")
        self.assertEqual(g.fix, "polymorph equip --harness claude links CLAUDE.md -> AGENTS.md")
        self.assertEqual(g.link_to, "AGENTS.md")

    def test_builtin_zcode_gaps_on_a_full_repo(self):
        self.assertEqual(self.gaps_for("zcode"),
                         [("rules", ".claude/rules/keep.md", "unsupported"),
                          ("rules", ".agents/rules/extra.md", "unsupported"),
                          ("memory", "CLAUDE.md", "unlinked-copy"),
                          ("claude_dir", ".claude/rules", "unsupported"),
                          ("claude_dir", ".claude/settings.json", "unsupported")])

    def test_zcode_reaches_skills_and_agents_md_at_rest(self):
        got = self.gaps_for("zcode")
        self.assertNotIn("AGENTS.md", [p for _, p, _ in got])
        self.assertNotIn(".agents/skills/alpha", [p for _, p, _ in got])

    def test_missing_table_is_none_and_unlisted(self):
        self.assertIsNone(inventory.load_coverage("gemini", self.repo))
        self.assertNotIn("gemini", inventory.available_tables(self.repo))


class CoverageTableTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)

    def write_table(self, text: str, name: str = "fake", repo_local: bool = True) -> Path:
        d = (self.repo / ".polymorph/basecamp/coverage") if repo_local else Path(tempfile.mkdtemp())
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{name}.toml"
        p.write_text(text)
        return p

    def test_repo_local_overrides_builtin_with_warning(self):
        self.write_table('schema = 1\nname = "zcode"\n\n[inventory]\nskills = ".zcode-skills"\n',
                         name="zcode")
        warnings: list[str] = []
        cap = inventory.load_coverage("zcode", self.repo, warn=warnings.append)
        self.assertEqual(cap.skills, ".zcode-skills")
        self.assertEqual(cap.memory, ())
        self.assertTrue(any("overrides" in w for w in warnings))

    def test_unknown_top_key_rejected(self):
        self.write_table('schema = 1\nname = "fake"\nextra = 1\n')
        with self.assertRaises(inventory.InventoryError):
            inventory.load_coverage("fake", self.repo)

    def test_unknown_inventory_kind_rejected(self):
        self.write_table('schema = 1\nname = "fake"\n\n[inventory]\nplugins = ".x"\n')
        with self.assertRaises(inventory.InventoryError):
            inventory.load_coverage("fake", self.repo)

    def test_memory_must_be_a_list_of_names(self):
        self.write_table('schema = 1\nname = "fake"\n\n[inventory]\nmemory = "AGENTS.md"\n')
        with self.assertRaises(inventory.InventoryError):
            inventory.load_coverage("fake", self.repo)

    def test_paths_must_be_plain_relative(self):
        self.write_table('schema = 1\nname = "fake"\n\n[inventory]\nskills = "/etc/skills"\n')
        with self.assertRaises(inventory.InventoryError):
            inventory.load_coverage("fake", self.repo)

    def test_name_must_match_file(self):
        self.write_table('schema = 1\nname = "other"\n', name="fake")
        with self.assertRaises(inventory.InventoryError):
            inventory.load_coverage("fake", self.repo)

    def test_explicit_dir_outside_trust_is_refused(self):
        self.write_table('schema = 1\nname = "fake"\n', repo_local=False)
        outside = self.write_table('schema = 1\nname = "fake2"\n', name="fake2", repo_local=False)
        with self.assertRaises(inventory.InventoryError):
            inventory.load_coverage("fake2", self.repo, coverage_dir=str(outside.parent))

    def test_explicit_dir_inside_trust_is_accepted(self):
        outside = self.write_table('schema = 1\nname = "fake2"\n', name="fake2", repo_local=False)
        cap = inventory.load_coverage("fake2", self.repo, coverage_dir=str(outside.parent),
                                          trust_dirs=(str(outside.parent),))
        self.assertEqual(cap.name, "fake2")


class CliTests(TempRepoTest):
    def run_cli(self, *argv: str) -> tuple[int, str]:
        from polymorph import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(list(argv))
        return rc, out.getvalue()

    def test_inventory_text_reports_gaps(self):
        rc, out = self.run_cli("inventory", "--repo", str(self.repo))
        self.assertEqual(rc, 0)
        self.assertIn("skills: 2", out)
        self.assertIn("gaps for zcode: 5", out)
        self.assertIn("- rules .claude/rules/keep.md: unsupported", out)
        self.assertIn("gaps for claude: 4", out)
        self.assertIn("- memory AGENTS.md: unlinked-copy", out)

    def test_inventory_json_is_structured(self):
        rc, out = self.run_cli("inventory", "--repo", str(self.repo), "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["inventory"]["memory"][0]["path"], "AGENTS.md")
        self.assertEqual(len(data["gaps"]), 9)  # zcode 5 + claude 4
        self.assertEqual({g["reason"] for g in data["gaps"] if g["harness"] == "claude"},
                         {"no-link", "unlinked-copy"})

    def test_inventory_named_harness_without_table_says_unknown(self):
        rc, out = self.run_cli("inventory", "--repo", str(self.repo), "--harness", "gemini")
        self.assertEqual(rc, 0)
        self.assertIn("no coverage table; nothing is known", out)


if __name__ == "__main__":
    unittest.main()
