"""Tests for the equip entrypoint: one onboarding run, one loadout."""
from __future__ import annotations

import contextlib
import hashlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from polymorph import equip

FIXTURE = Path(__file__).parent / "fixture_repo"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def equip_repo(root: Path, git_init: bool = False, track_all: bool = False) -> Path:
    shutil.copytree(FIXTURE, root, dirs_exist_ok=True)
    (root / "AGENTS.md").write_text("instructions\n")
    (root / ".agents/skills/alpha").mkdir(parents=True)
    (root / ".agents/skills/alpha/SKILL.md").write_text("skill\n")
    (root / ".polymorph/basecamp/env.toml").write_text(
        'schema = 1\n\n[vault]\nunset = ["SSH_AUTH_SOCK"]\n\n'
        '[harness.zcode]\nexports = ["POLYMORPH_HARNESS=zcode"]\n')
    if git_init:
        git(root, "init", "-q")
        if track_all:
            git(root, "-c", "commit.gpgsign=false", "-c", "user.email=t@t", "-c", "user.name=t",
                "add", "-A")
        else:
            git(root, "-c", "commit.gpgsign=false", "-c", "user.email=t@t", "-c", "user.name=t",
                "add", ".agents")
        git(root, "-c", "commit.gpgsign=false", "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-qm", "base")
    return root


class EquipTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = equip_repo(Path(tmp.name))

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        from polymorph import cli

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_equip_writes_reports_and_records_provenance(self):
        rc, out, err = self.run_cli("equip", "--repo", str(self.repo), "--harness", "zcode")
        self.assertEqual(rc, 0, (out, err))
        self.assertIn("[zcode] wrote .zcode/config.json", out)
        self.assertIn("- memory AGENTS.md", out)
        self.assertIn("- skills .agents/skills/alpha", out)
        self.assertIn("env contract:", out)
        self.assertIn("export POLYMORPH_HARNESS=zcode", out)
        self.assertIn("unset SSH_AUTH_SOCK", out)
        self.assertIn("manifest: .polymorph/basecamp/loadout.toml", out)
        config = self.repo / ".zcode/config.json"
        self.assertTrue(config.is_file())
        want = "sha256:" + hashlib.sha256(config.read_bytes()).hexdigest()
        self.assertIn(want, (self.repo / ".polymorph/basecamp/loadout.toml").read_text())
        self.assertIn("generated from .agents/settings.json via mapping zcode",
                      (self.repo / ".polymorph/basecamp/loadout.toml").read_text())

    def test_equip_report_gaps_for_provided_but_uncovered(self):
        (self.repo / "CLAUDE.md").write_text("instructions\n")
        rc, out, _ = self.run_cli("equip", "--repo", str(self.repo), "--harness", "zcode")
        self.assertEqual(rc, 0)
        self.assertIn("gaps (will be silently ignored): 1", out)
        self.assertIn("- memory CLAUDE.md", out)

    def test_dry_run_writes_nothing(self):
        before = sorted(p.relative_to(self.repo).as_posix() for p in self.repo.rglob("*"))
        rc, out, _ = self.run_cli("equip", "--repo", str(self.repo), "--harness", "zcode",
                                  "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("would write .zcode/config.json", out)
        self.assertFalse((self.repo / ".zcode/config.json").exists())
        self.assertFalse((self.repo / ".polymorph/basecamp/loadout.toml").exists())
        self.assertEqual(sorted(p.relative_to(self.repo).as_posix() for p in self.repo.rglob("*")),
                         before)

    def test_second_equip_reports_unchanged(self):
        self.run_cli("equip", "--repo", str(self.repo), "--harness", "zcode")
        rc, out, _ = self.run_cli("equip", "--repo", str(self.repo), "--harness", "zcode")
        self.assertEqual(rc, 0)
        self.assertIn("unchanged .zcode/config.json", out)

    def test_undecided_reports_not_written(self):
        cfg = self.repo / ".polymorph/basecamp/config.toml"
        cfg.write_text('enabled = ["nopost"]\n')
        rc, out, _ = self.run_cli("equip", "--repo", str(self.repo), "--harness", "nopost")
        self.assertEqual(rc, 0)
        self.assertIn("NOT WRITTEN .nopost/config.json", out)
        self.assertFalse((self.repo / ".nopost/config.json").exists())

    def test_no_coverage_table_claims_nothing(self):
        text = (FIXTURE / ".polymorph/basecamp/mappings/zcode.toml").read_text()
        text = text.replace('name = "zcode"', 'name = "fake"').replace(
            'config_path = ".zcode/config.json"', 'config_path = ".fake/config.json"')
        (self.repo / ".polymorph/basecamp/mappings/fake.toml").write_text(text)
        cfg = self.repo / ".polymorph/basecamp/config.toml"
        cfg.write_text('enabled = ["fake"]\n')
        rc, out, _ = self.run_cli("equip", "--repo", str(self.repo), "--harness", "fake")
        self.assertEqual(rc, 0, out)
        self.assertIn("coverage: no coverage table; nothing is known", out)
        self.assertNotIn("honoured surface:\n  -", out)
        self.assertFalse([l for l in out.splitlines() if l.strip().startswith("- ")])


class PostureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = equip_repo(Path(tmp.name), git_init=True)

    def run_cli(self, *argv: str) -> tuple[int, str]:
        from polymorph import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(list(argv))
        return rc, out.getvalue()

    def test_contributor_posture_suggests_and_appends_on_request(self):
        rc, out = self.run_cli("equip", "--repo", str(self.repo), "--harness", "zcode")
        self.assertEqual(rc, 0)
        self.assertIn("posture: contributor", out)
        self.assertIn("pass --gitignore to append", out)
        self.assertFalse((self.repo / ".gitignore").exists())
        rc, out = self.run_cli("equip", "--repo", str(self.repo), "--harness", "zcode",
                               "--gitignore")
        self.assertEqual(rc, 0)
        self.assertIn("appended the marked regenerable-artifacts section", out)
        text = (self.repo / ".gitignore").read_text()
        self.assertIn("# polymorph equip: issued artifacts below are regenerable", text)
        self.assertIn(".zcode/config.json", text)
        rc, out = self.run_cli("equip", "--repo", str(self.repo), "--harness", "zcode",
                               "--gitignore")
        self.assertEqual(rc, 0)
        self.assertNotIn("appended", out)
        self.assertEqual(text, (self.repo / ".gitignore").read_text())

    def test_maintainer_posture_commits_artifacts_without_gitignore(self):
        git(self.repo, "-c", "commit.gpgsign=false", "-c", "user.email=t@t", "-c", "user.name=t",
            "add", "-A")
        git(self.repo, "-c", "commit.gpgsign=false", "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-qm", "all tracked")
        rc, out = self.run_cli("equip", "--repo", str(self.repo), "--harness", "zcode")
        self.assertEqual(rc, 0)
        self.assertIn("posture: maintainer", out)
        self.assertNotIn("gitignore", out)


if __name__ == "__main__":
    unittest.main()
