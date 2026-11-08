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
        rc = main(["manage", "check", *argv])
    return rc, buf.getvalue()


class CheckTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        shutil.copytree(FIXTURE, self.repo)
        # use the built-in (verified, fixture-backed) zcode mapping
        shutil.rmtree(self.repo / ".polymorph/basecamp/mappings")
        generate(self.repo)

    def codes(self, **kw):
        return {f.code for f in check.run_check(self.repo, **kw)}

    def test_clean_repo_exits_zero(self):
        rc, out = run("--repo", str(self.repo))
        self.assertEqual(rc, 0, out)
        # the shipped zcode mapping is delivery = "direct" with ask = false:
        # nothing sets GUARD_ASK_ENFORCED for a bare hook, and check says so (#34)
        self.assertIn("direct-ask-unenforceable", out)
        self.assertIn("0 error(s), 2 warning(s)", out)
        self.assertIn("direct-json-answers-ignored", out)
        rc, out = run("--repo", str(self.repo), "--strict")
        self.assertEqual(rc, 1, out)

    def test_fail_open_target_is_a_warning(self):
        (self.repo / ".polymorph/basecamp/config.toml").write_text('enabled = ["zcode"]\nfail_open = ["zcode"]\n')
        findings = [f for f in check.run_check(self.repo) if f.code == "fail-open-enabled"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warning")

    def test_direct_mapping_that_ignores_json_answers_is_a_warning(self):
        # zcode: delivery = "direct", json_answers = false (live: a JSON deny ran the call)
        findings = [f for f in check.run_check(self.repo) if f.code == "direct-json-answers-ignored"]
        self.assertEqual([f.target for f in findings], ["zcode"])
        self.assertIn("exit 2", findings[0].message)

    def test_no_targets(self):
        (self.repo / ".polymorph/basecamp/config.toml").unlink()
        rc, out = run("--repo", str(self.repo))
        self.assertEqual(rc, 1)
        self.assertIn("no-targets", out)
        self.assertEqual(self.codes(harnesses=["zcode"]) & {"no-targets"}, set())

    REQUIRED_TABLES = ("tools", "events", "capabilities", "payload_in", "payload_out")

    def _local_zcode_without(self, table):
        """A repo-local zcode mapping (the built-in text) with one table removed."""
        keep, skip = [], False
        for line in (Path(check.BUILTIN_DIR) / "zcode.toml").read_text().splitlines():
            if line.startswith("["):
                skip = line.strip() == f"[{table}]"
            if not skip:
                keep.append(line)
        d = self.repo / ".polymorph/basecamp/mappings"
        d.mkdir(exist_ok=True)
        (d / "zcode.toml").write_text("\n".join(keep) + "\n")
        return d / "zcode.toml"

    def test_mapping_invalid(self):
        self._local_zcode_without("capabilities")
        fs = [f for f in check.run_check(self.repo) if f.code == "mapping-invalid"]
        self.assertTrue(fs)
        self.assertTrue(all(f.severity == "error" and f.target == "zcode" for f in fs))
        self.assertEqual(run("--repo", str(self.repo))[0], 1)

    def test_missing_table_rejected_alike_by_generate_validate_check(self):
        from polymorph.mapping import MappingError
        from polymorph.validate import validate_mapping
        for table in self.REQUIRED_TABLES:
            with self.subTest(table=table):
                f = self._local_zcode_without(table)
                self.assertTrue(validate_mapping(f), "validate accepted it")
                with self.assertRaises(MappingError):
                    generate(self.repo, ["zcode"])
                fs = [x for x in check.run_check(self.repo) if x.code == "mapping-invalid"]
                self.assertTrue(fs, "check accepted it")

    def test_mapping_unloadable(self):
        d = self.repo / ".polymorph/basecamp/mappings"
        d.mkdir()
        (d / "zcode.toml").write_text("not = [toml")
        self.assertIn("mapping-invalid", self.codes())

    def test_unverified_warning_and_strict(self):
        d = self.repo / ".polymorph/basecamp/mappings"
        d.mkdir()
        text = (Path(check.BUILTIN_DIR) / "zcode.toml").read_text()
        text = "\n".join(l for l in text.splitlines() if not l.startswith("verified"))
        (d / "zcode.toml").write_text(text + "\n")
        generate(self.repo)
        self.assertIn("mapping-unverified", self.codes())
        self.assertEqual(run("--repo", str(self.repo))[0], 0)
        self.assertEqual(run("--repo", str(self.repo), "--strict")[0], 1)
        self.assertIn("mapping-untested-live", self.codes())

    def test_direct_ask_false_is_reported_unenforceable(self):
        # issue #34: the one shipped mapping with ask = false is delivery = "direct",
        # whose hooks run bare and see no GUARD_* variables at all
        fs = [f for f in check.run_check(self.repo) if f.code == "direct-ask-unenforceable"]
        self.assertTrue(fs)
        self.assertTrue(all(f.severity == "warning" and f.target == "zcode" for f in fs))
        self.assertIn("GUARD_ASK_ENFORCED", fs[0].message)

    def test_an_adapter_mapping_that_cannot_express_a_deny_is_invalid(self):
        # issue #35: no payload_out.deny with exit_2_blocks and json_answers both false;
        # validate refuses it, so check reports mapping-invalid (an error), not a warning
        d = self.repo / ".polymorph/basecamp/mappings"
        d.mkdir(exist_ok=True)
        base = (Path(check.BUILTIN_DIR) / "gemini.toml").read_text()
        cases = {
            "cannot deny": lambda t: t.replace('deny = { exit = 2, stderr = "{reason}" }\n', "")
                                       .replace("exit_2_blocks = true", "exit_2_blocks = false")
                                       .replace("json_answers = true", "json_answers = false"),
            "json answers can carry the deny": lambda t: t.replace('deny = { exit = 2, stderr = "{reason}" }\n', ""),
            "exit 2 blocks": lambda t: t,
        }
        for name, mut in cases.items():
            with self.subTest(case=name):
                for p in d.glob("gemini.toml"):
                    p.unlink()
                (d / "gemini.toml").write_text(mut(base))
                findings = [f for f in check.run_check(self.repo, harnesses=["gemini"])
                            if f.code == "mapping-invalid" and "payload_out.deny" in f.message]
                if name == "cannot deny":
                    self.assertTrue(findings)
                    self.assertTrue(all(f.severity == "error" and f.target == "gemini" for f in findings))
                else:
                    self.assertEqual(findings, [])

    def test_a_direct_mapping_needs_no_deny_route(self):
        # direct delivery bypasses the adapter, so its empty payload_out expresses nothing
        findings = [f for f in check.run_check(self.repo)
                    if f.code == "mapping-invalid" and "payload_out.deny" in f.message]
        self.assertEqual(findings, [])

    def test_a_deny_route_on_an_exit_the_mapping_calls_non_blocking_is_flagged(self):
        # issue #49: gemini's deny/ask entries render as exit 2, the only code
        # validate allows; pairing that route with exit_2_blocks = false means the
        # route relies on the one code the mapping itself says does not block
        d = self.repo / ".polymorph/basecamp/mappings"
        d.mkdir(exist_ok=True)
        base = (Path(check.BUILTIN_DIR) / "gemini.toml").read_text()
        (d / "gemini.toml").write_text(base.replace("exit_2_blocks = true", "exit_2_blocks = false"))
        fs = [f for f in check.run_check(self.repo, harnesses=["gemini"])
              if f.code == "exit2-unhonoured"]
        self.assertTrue(fs)
        self.assertTrue(all(f.severity == "warning" and f.target == "gemini" for f in fs))
        self.assertIn("exit_2_blocks", fs[0].message)
        # the shipped gemini (exit_2_blocks = true, deny exit 2) is coherent, and a
        # mapping with no deny/ask entry has no such route to be incoherent about
        (d / "gemini.toml").write_text(base)
        self.assertEqual([f for f in check.run_check(self.repo, harnesses=["gemini"])
                          if f.code == "exit2-unhonoured"], [])
        (d / "gemini.toml").write_text(base.replace('deny = { exit = 2, stderr = "{reason}" }\n', "")
                                              .replace("exit_2_blocks = true", "exit_2_blocks = false"))
        self.assertEqual([f for f in check.run_check(self.repo, harnesses=["gemini"])
                          if f.code == "exit2-unhonoured"], [])

    def test_a_direct_mapping_is_never_flagged_as_exit2_unhonoured(self):
        # direct hooks run bare; their payload_out is empty by validation
        fs = [f for f in check.run_check(self.repo) if f.code == "exit2-unhonoured"]
        self.assertEqual(fs, [])

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

    def test_undecided_mismatch(self):
        d = self.repo / ".polymorph/basecamp/mappings"
        d.mkdir()
        shutil.copy(FIXTURE / ".polymorph/basecamp/mappings/nopost.toml", d)
        fs = check.run_check(self.repo, harnesses=["nopost"])
        gaps = [f for f in fs if f.code == "decision-undecided"]
        self.assertTrue(gaps)
        self.assertTrue(all(f.severity == "error" and f.target == "nopost" for f in gaps))

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

    def test_generated_drift_names_a_command_that_exists(self):
        # generate moved under `manage`; the old `polymorph generate` is a usage error
        (self.repo / ".zcode/config.json").unlink()
        drift = [f for f in check.run_check(self.repo) if f.code == "generated-drift"]
        self.assertTrue(drift)
        self.assertIn("polymorph manage generate", drift[0].message)

    def test_mapping_override_note(self):
        d = self.repo / ".polymorph/basecamp/mappings"
        d.mkdir()
        shutil.copy(Path(check.BUILTIN_DIR) / "zcode.toml", d / "zcode.toml")
        fs = [f for f in check.run_check(self.repo) if f.code == "mapping-override"]
        self.assertTrue(fs)
        self.assertTrue(all(f.severity == "note" and f.target == "zcode" for f in fs))

    def test_loadout_drift_and_invalid(self):
        # equip pins the mapping bytes it consumed; the adapter re-reads the
        # mapping at run time, so a swap after equip must be visible to check
        d = self.repo / ".polymorph/basecamp/mappings"
        d.mkdir()
        shutil.copy(Path(check.BUILTIN_DIR) / "zcode.toml", d / "zcode.toml")
        mf = self.repo / ".polymorph/basecamp/loadout.toml"
        good = "sha256:" + hashlib.sha256((d / "zcode.toml").read_bytes()).hexdigest()
        mp = json.dumps(str(d / "zcode.toml"))
        mf.write_text(f'schema = 1\n[harness.zcode]\n".zcode/config.json" = '
                      f'{{hash = "sha256:x", source = "s", mapping-path = {mp}, '
                      f'mapping-hash = "{good}"}}\n')
        self.assertNotIn("loadout-drift", self.codes())
        (d / "zcode.toml").write_text((d / "zcode.toml").read_text() + "\n# changed\n")
        self.assertIn("loadout-drift", self.codes())
        fs = [f for f in check.run_check(self.repo) if f.code == "loadout-drift"]
        self.assertTrue(all(f.severity == "warning" for f in fs))
        mf.write_text(f'schema = 1\n[harness.zcode]\n".zcode/config.json" = '
                      f'{{hash = "sha256:x", source = "s", mapping-path = {mp}}}\n')
        fs = [f for f in check.run_check(self.repo) if f.code == "loadout-invalid"]
        self.assertTrue(fs and fs[0].severity == "error")

    def test_fixtures_missing_and_failed(self):
        # a built-in mapping with no fixtures dir -> warning
        self.assertNotIn("fixtures-missing", self.codes())
        self.assertIn("fixtures-missing", self.codes(harnesses=["gemini"]))
        # a failing fixture -> error: replayed from a temp copy, never the shipped one
        from unittest import mock

        from polymorph import conformance
        orig = conformance.fixture_files
        with tempfile.TemporaryDirectory() as d:
            fxdir = Path(d) / "fixtures" / "zcode"
            fxdir.mkdir(parents=True)
            data = json.loads((conformance.DATA_ROOT / "fixtures/zcode/deny.json").read_text())
            data["native_out"]["exit"] = 99
            (fxdir / "deny.json").write_text(json.dumps(data))
            with mock.patch.object(conformance, "fixture_files",
                                   lambda harness=None, root=None: orig(harness, Path(d))):
                fs = [f for f in check.run_check(self.repo) if f.code == "fixture-failed"]
        self.assertTrue(fs and fs[0].severity == "error")

    def test_local_mapping_fixtures_skipped_with_note(self):
        d = self.repo / ".polymorph/basecamp/mappings"
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
