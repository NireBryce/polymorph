import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from polymorph import check
from polymorph.cli import main
from polymorph.generate import generate

FIXTURE = Path(__file__).resolve().parent / "fixture_repo"


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
            h.update(str(p.stat().st_mode).encode())
    return h.hexdigest()


def run(*argv) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["check", *argv])
    return rc, buf.getvalue()


class CheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        shutil.copytree(FIXTURE, self.repo)
        # use the built-in (verified, fixture-backed) zcode mapping
        shutil.rmtree(self.repo / ".agents/polymorph/mappings")
        generate(self.repo)

    def codes(self, **kw):
        return {f.code for f in check.run_check(self.repo, **kw)}

    def test_clean_repo_exits_zero(self):
        rc, out = run("--repo", str(self.repo))
        self.assertEqual(rc, 0, out)
        rc, out = run("--repo", str(self.repo), "--strict")
        self.assertEqual(rc, 0, out)
        self.assertIn("0 error(s), 0 warning(s)", out)

    def test_no_targets(self):
        (self.repo / ".agents/polymorph/config.toml").unlink()
        rc, out = run("--repo", str(self.repo))
        self.assertEqual(rc, 1)
        self.assertIn("no-targets", out)
        self.assertEqual(self.codes(harnesses=["zcode"]) & {"no-targets"}, set())

    def test_mapping_invalid(self):
        d = self.repo / ".agents/polymorph/mappings"
        d.mkdir()
        shutil.copy(FIXTURE / ".agents/polymorph/mappings/zcode.toml", d)  # lacks required tables
        fs = [f for f in check.run_check(self.repo) if f.code == "mapping-invalid"]
        self.assertTrue(fs)
        self.assertTrue(all(f.severity == "error" and f.target == "zcode" for f in fs))
        self.assertEqual(run("--repo", str(self.repo))[0], 1)

    def test_mapping_unloadable(self):
        d = self.repo / ".agents/polymorph/mappings"
        d.mkdir()
        (d / "zcode.toml").write_text("not = [toml")
        self.assertIn("mapping-invalid", self.codes())

    def test_unverified_warning_and_strict(self):
        d = self.repo / ".agents/polymorph/mappings"
        d.mkdir()
        text = (Path(check.BUILTIN_DIR) / "zcode.toml").read_text()
        text = "\n".join(l for l in text.splitlines() if not l.startswith("verified"))
        (d / "zcode.toml").write_text(text + "\n")
        generate(self.repo)
        self.assertIn("mapping-unverified", self.codes())
        self.assertEqual(run("--repo", str(self.repo))[0], 0)
        self.assertEqual(run("--repo", str(self.repo), "--strict")[0], 1)
        self.assertIn("mapping-untested-live", self.codes())

    def test_hook_missing_and_not_executable(self):
        hooks = self.repo / ".agents/hooks"
        (hooks / "session-start.sh").unlink()
        os.chmod(hooks / "edit-check-posttooluse.sh", 0o644)
        fs = {f.code: f for f in check.run_check(self.repo)}
        self.assertEqual(fs["hook-missing"].severity, "error")
        self.assertEqual(fs["hook-not-executable"].severity, "error")
        self.assertEqual(fs["hook-missing"].file, ".agents/hooks/session-start.sh")

    def test_hook_outside_hooks_dir(self):
        p = self.repo / ".agents/settings.json"
        data = json.loads(p.read_text())
        data["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "/bin/true"
        p.write_text(json.dumps(data))
        fs = check.run_check(self.repo)
        self.assertIn("hook-outside-hooks-dir", {f.code for f in fs})
        self.assertEqual(run("--repo", str(self.repo))[0], 1)

    def test_settings_missing(self):
        (self.repo / ".agents/settings.json").unlink()
        self.assertIn("settings-invalid", self.codes())

    def test_hook_no_equivalent(self):
        d = self.repo / ".agents/polymorph/mappings"
        d.mkdir()
        shutil.copy(FIXTURE / ".agents/polymorph/mappings/nopost.toml", d)
        fs = check.run_check(self.repo, harnesses=["nopost"])
        gaps = [f for f in fs if f.code == "hook-no-equivalent"]
        self.assertTrue(gaps)
        self.assertTrue(all(f.severity == "warning" and f.target == "nopost" for f in gaps))

    def test_setting_unmapped_note(self):
        fs = [f for f in check.run_check(self.repo) if f.code == "setting-unmapped"]
        self.assertTrue(fs)
        self.assertTrue(all(f.severity == "note" for f in fs))

    def test_generated_drift_changed_and_missing(self):
        out = self.repo / ".zcode/config.json"
        out.write_text("{}\n")
        self.assertIn("generated-drift", self.codes())
        out.unlink()
        self.assertIn("generated-drift", self.codes())
        self.assertEqual(run("--repo", str(self.repo))[0], 1)

    def test_fixtures_missing_and_failed(self):
        # a built-in mapping with no fixtures dir -> warning
        self.assertNotIn("fixtures-missing", self.codes())
        self.assertIn("fixtures-missing", self.codes(harnesses=["gemini"]))
        # a failing fixture -> error (restore afterwards)
        fx = check.BUILTIN_DIR.parent / "fixtures/zcode/deny.json"
        orig = fx.read_text()
        self.addCleanup(fx.write_text, orig)
        data = json.loads(orig)
        data["native_out"]["exit"] = 99
        fx.write_text(json.dumps(data))
        fs = [f for f in check.run_check(self.repo) if f.code == "fixture-failed"]
        self.assertTrue(fs and fs[0].severity == "error")

    def test_local_mapping_fixtures_skipped_with_note(self):
        d = self.repo / ".agents/polymorph/mappings"
        d.mkdir()
        shutil.copy(Path(check.BUILTIN_DIR) / "zcode.toml", d)
        fs = check.run_check(self.repo)
        self.assertIn("fixtures-skipped", {f.code for f in fs})

    def test_json_format(self):
        rc, out = run("--repo", str(self.repo), "--format", "json")
        doc = json.loads(out)
        self.assertEqual(set(doc), {"findings", "summary"})
        self.assertEqual(set(doc["summary"]), {"error", "warning", "note"})
        self.assertEqual(set(doc["findings"][0]), {"severity", "code", "target", "message", "file"})

    def test_sarif_format(self):
        (self.repo / ".zcode/config.json").unlink()
        rc, out = run("--repo", str(self.repo), "--format", "sarif")
        self.assertEqual(rc, 1)
        doc = json.loads(out)
        self.assertEqual(doc["version"], "2.1.0")
        self.assertIn("sarif", doc["$schema"])
        run_ = doc["runs"][0]
        self.assertEqual(run_["tool"]["driver"]["name"], "polymorph")
        rule_ids = {r["id"] for r in run_["tool"]["driver"]["rules"]}
        self.assertTrue(run_["results"])
        for r in run_["results"]:
            self.assertIn(r["level"], {"error", "warning", "note"})
            self.assertIn(r["ruleId"], rule_ids)
        drift = [r for r in run_["results"] if r["ruleId"] == "generated-drift"][0]
        self.assertEqual(drift["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
                         ".zcode/config.json")

    def test_text_format_lines(self):
        rc, out = run("--repo", str(self.repo))
        self.assertIn("note mapping-untested-live zcode:", out)
        self.assertTrue(out.rstrip().splitlines()[-1].endswith("note(s)"))

    def test_does_not_modify_repo_or_run_hooks(self):
        marker = self.repo / "marker"
        for h in (self.repo / ".agents/hooks").iterdir():
            h.write_text(f"#!/bin/sh\ntouch {marker}\n")
            h.chmod(0o755)
        (self.repo / ".zcode/config.json").unlink()  # exercise the drift path too
        before = tree_hash(self.repo)
        for fmt in ("text", "json", "sarif"):
            run("--repo", str(self.repo), "--format", fmt)
        self.assertEqual(tree_hash(self.repo), before)
        self.assertFalse(marker.exists())
        self.assertFalse((self.repo / ".zcode/config.json").exists())


if __name__ == "__main__":
    unittest.main()
