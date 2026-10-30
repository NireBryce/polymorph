import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from polymorph import cli, mapping  # noqa: E402
from polymorph.generate import generate  # noqa: E402
from polymorph.mapping import MappingError, load_mapping  # noqa: E402
from polymorph.reader_claude import ReaderError, classify_matcher, read_claude  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixture_repo"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        shutil.copytree(FIXTURE, self.repo)
        self.nobuiltin = self.tmp / "nobuiltin"
        self.nobuiltin.mkdir()
        old = mapping.BUILTIN_DIR
        mapping.BUILTIN_DIR = self.nobuiltin
        self.addCleanup(setattr, mapping, "BUILTIN_DIR", old)


class ReaderTests(Base):
    def test_matcher_classes(self):
        self.assertEqual(classify_matcher("Bash"), ("shell",))
        self.assertEqual(classify_matcher("Edit|Write|MultiEdit"), ("edit",))
        self.assertEqual(classify_matcher("Read|Grep|Glob"), ("read",))
        self.assertEqual(classify_matcher("mcp__foo__bar"), ("mcp",))
        self.assertEqual(classify_matcher("Task"), ("other",))
        self.assertEqual(classify_matcher("Bash|Task"), ("shell", "other"))
        self.assertEqual(classify_matcher(None), ())

    def test_real_shape(self):
        c = read_claude(self.repo)
        ev = [(h.event, h.classes, h.script) for h in c.hooks]
        self.assertIn(("session_start", (), ".agents/hooks/session-start.sh"), ev)
        self.assertIn(("pre_tool", ("shell",), ".agents/hooks/secrets-guard-pretooluse.sh"), ev)
        self.assertIn(("post_tool", ("edit",), ".agents/hooks/edit-check-posttooluse.sh"), ev)
        self.assertEqual(c.hooks[0].timeout, 60)

    def test_unmapped_reported(self):
        u = " ".join(read_claude(self.repo).unmapped)
        for key in ("attribution", "permissions.allow", "permissions.deny", "hooks.Stop"):
            self.assertIn(key, u)

    def test_script_outside_hooks_refused(self):
        p = self.repo / ".agents/settings.json"
        d = json.loads(p.read_text())
        d["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "/bin/sh"
        p.write_text(json.dumps(d))
        with self.assertRaises(ReaderError):
            read_claude(self.repo)

    def test_symlink_escape_refused(self):
        os.symlink("/bin/sh", self.repo / ".agents/hooks/evil.sh")
        p = self.repo / ".agents/settings.json"
        d = json.loads(p.read_text())
        d["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "$CLAUDE_PROJECT_DIR/.agents/hooks/evil.sh"
        p.write_text(json.dumps(d))
        with self.assertRaises(ReaderError):
            read_claude(self.repo)

    def test_command_with_args_refused(self):
        p = self.repo / ".agents/settings.json"
        d = json.loads(p.read_text())
        d["hooks"]["SessionStart"][0]["hooks"][0]["command"] = "sh -c 'x'"
        p.write_text(json.dumps(d))
        with self.assertRaises(ReaderError):
            read_claude(self.repo)


class MappingTests(Base):
    def test_loads_repo_mapping(self):
        m = load_mapping("zcode", self.repo)
        self.assertEqual(m.tools["edit"], ("Edit", "Write", "MultiEdit"))
        self.assertEqual(m.tools["shell"], ("Bash",))

    def test_unknown_key_rejected(self):
        with self.assertRaises(MappingError):
            mapping.parse_mapping({"schema": 1, "name": "x", "config_path": "a", "format": "json",
                                   "template": "t", "bogus": 1}, "x")
        with self.assertRaises(MappingError):
            mapping.parse_mapping({"schema": 1, "name": "x", "config_path": "a", "format": "json",
                                   "template": "t", "events": {"nope": "X"}}, "x")

    def test_schema_checked(self):
        with self.assertRaises(MappingError):
            mapping.parse_mapping({"schema": 2, "name": "x"}, "x")

    def test_override_warns(self):
        (self.nobuiltin / "zcode.toml").write_text((self.repo / ".agents/polymorph/mappings/zcode.toml").read_text())
        msgs = []
        load_mapping("zcode", self.repo, warn=msgs.append)
        self.assertTrue(any("overrides" in m for m in msgs))

    def test_builtin_fallback_no_warning(self):
        (self.nobuiltin / "other.toml").write_text(
            'schema = 1\nname = "other"\nconfig_path = "a.json"\nformat = "json"\ntemplate = "claude-like"\n')
        msgs = []
        self.assertEqual(load_mapping("other", self.repo, warn=msgs.append).name, "other")
        self.assertEqual(msgs, [])

    def test_trust_dir_rule(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        shutil.copy(self.repo / ".agents/polymorph/mappings/zcode.toml", outside)
        with self.assertRaises(MappingError):
            load_mapping("zcode", self.repo, mappings_dir=outside)
        self.assertEqual(load_mapping("zcode", self.repo, outside, (outside,)).name, "zcode")

    def test_bad_name(self):
        with self.assertRaises(MappingError):
            load_mapping("../x", self.repo)


class GenerateTests(Base):
    def test_default_targets_from_harnesses_toml(self):
        rs = generate(self.repo)
        self.assertEqual([r.harness for r in rs], ["zcode"])
        doc = json.loads((self.repo / ".zcode/config.json").read_text())
        self.assertTrue(doc["hooks"]["enabled"])
        pre = doc["hooks"]["events"]["PreToolUse"]
        self.assertEqual(pre[0]["matcher"], "Bash")
        cmd = pre[0]["hooks"][0]["command"]
        self.assertEqual(
            cmd,
            'python -m polymorph adapter --harness zcode -- '
            '"$CLAUDE_PROJECT_DIR/.agents/hooks/secrets-guard-pretooluse.sh"',
        )
        ss = doc["hooks"]["events"]["SessionStart"][0]
        self.assertNotIn("matcher", ss)
        self.assertEqual(ss["hooks"][0]["timeout"], 60)

    def test_preserves_other_keys(self):
        (self.repo / ".zcode").mkdir()
        (self.repo / ".zcode/config.json").write_text('{"theme": "dark"}')
        generate(self.repo)
        self.assertEqual(json.loads((self.repo / ".zcode/config.json").read_text())["theme"], "dark")

    def test_check_drift(self):
        r = generate(self.repo, check=True)[0]
        self.assertTrue(r.drift)
        self.assertFalse((self.repo / ".zcode/config.json").exists())
        generate(self.repo)
        self.assertFalse(generate(self.repo, check=True)[0].drift)
        (self.repo / ".zcode/config.json").write_text("{}")
        self.assertTrue(generate(self.repo, check=True)[0].drift)

    def test_gaps_and_warnings(self):
        r = generate(self.repo, ["nopost"])[0]
        gaps = " ".join(r.gaps)
        self.assertIn("no post_tool event", gaps)
        self.assertIn("edit-check-posttooluse.sh", gaps)
        self.assertIn("no nopost tool for class(es) read", gaps)
        self.assertTrue(any("verified" in w for w in r.warnings))
        self.assertIn("attribution", " ".join(r.unmapped))
        doc = json.loads((self.repo / ".nopost/config.json").read_text())
        self.assertNotIn("PostToolUse", doc["hooks"]["events"])
        self.assertEqual(list(doc["hooks"]["events"]), ["start", "before"])

    def test_verified_no_warning(self):
        self.assertEqual(generate(self.repo, ["zcode"])[0].warnings, [])


class CliTests(Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_generate_then_check(self):
        rc, _, _ = self.run_cli("generate", "--repo", str(self.repo), "--check")
        self.assertEqual(rc, 1)
        rc, out, _ = self.run_cli("generate", "--repo", str(self.repo))
        self.assertEqual(rc, 0)
        self.assertIn("unmapped setting: attribution", out)
        rc, _, _ = self.run_cli("generate", "--repo", str(self.repo), "--check")
        self.assertEqual(rc, 0)

    def test_missing_mapping_is_error(self):
        rc, _, err = self.run_cli("generate", "--repo", str(self.repo), "--harness", "nope")
        self.assertEqual(rc, 2)
        self.assertIn("nope", err)

    def test_adapter_args_after_double_dash(self):
        self.assertTrue(cli._parser().parse_args(["adapter", "--harness", "x", "--", "s.sh"]).script == ["s.sh"])


if __name__ == "__main__":
    unittest.main()


class SettingsShapeTest(unittest.TestCase):
    def test_claude_settings_template_has_no_wrapper(self):
        from polymorph.generate import _claude_settings, _claude_like
        from polymorph.canon import Canon, Hook
        canon = Canon(hooks=(Hook("pre_tool", ("shell",), ".agents/hooks/x.sh", None),), unmapped=())
        m = load_mapping("claude", Path("/nonexistent-repo"))
        flat = json.loads(_claude_settings(canon, m, {})[0])
        self.assertNotIn("enabled", flat["hooks"])
        group = flat["hooks"]["PreToolUse"][0]
        self.assertEqual(group["matcher"], "Bash")
        self.assertNotIn("enabled", group["hooks"][0])
        wrapped = json.loads(_claude_like(canon, m, {})[0])
        self.assertTrue(wrapped["hooks"]["enabled"])


class DirectDeliveryTests(Base):
    def _write(self, **extra):
        import tomllib
        raw = tomllib.loads((Path(__file__).resolve().parents[1] / "src" / "polymorph" / "mappings" / "zcode.toml").read_text())
        raw.update(extra)
        return raw

    def test_default_is_adapter(self):
        out = generate(self.repo, ["zcode"], check=True)[0].content
        self.assertIn("python -m polymorph adapter", out)

    def test_direct_emits_bare_quoted_script(self):
        d = self.repo / ".agents/polymorph/mappings"
        text = (d / "zcode.toml").read_text().replace('template =', 'delivery = "direct"\ntemplate =', 1)
        (d / "zcode.toml").write_text(text)
        out = generate(self.repo, ["zcode"], check=True)[0].content
        self.assertNotIn("polymorph adapter", out)
        self.assertIn('"command": "\\"$CLAUDE_PROJECT_DIR/.agents/hooks/session-start.sh\\""', out)

    def test_builtin_zcode_and_claude_are_direct(self):
        mapping.BUILTIN_DIR = Path(mapping.__file__).resolve().parent / "mappings"
        for n in ("zcode", "claude"):
            self.assertEqual(load_mapping(n, self.repo.parent).delivery, "direct")

    def test_validate_rejects_non_identity(self):
        from polymorph.validate import validate_mapping
        base = self._write()
        self.assertEqual(validate_mapping(base), [])
        bad_in = self._write()
        bad_in["payload_in"] = dict(bad_in["payload_in"], cwd="workingDir")
        self.assertTrue(any("payload_in.cwd" in e for e in validate_mapping(bad_in)))
        bad_out = self._write(payload_out={"deny": {"exit": 2}})
        self.assertTrue(any("payload_out" in e for e in validate_mapping(bad_out)))
        ext = self._write()
        ext["payload_in"] = dict(ext["payload_in"], extractor="apply_patch_paths")
        self.assertTrue(any("extractor" in e for e in validate_mapping(ext)))
        self.assertTrue(validate_mapping(self._write(delivery="sideways")))
        # the same non-identity mapping is fine under the adapter
        adapter = self._write(delivery="adapter", payload_out={"deny": {"exit": 2}})
        self.assertEqual(validate_mapping(adapter), [])

    def test_parse_rejects_direct_non_identity(self):
        raw = self._write(payload_out={"deny": {"exit": 2}})
        with self.assertRaises(MappingError):
            mapping.parse_mapping(raw, "x")

    def test_check_reports_error(self):
        from polymorph import check
        d = self.repo / ".agents/polymorph/mappings"
        (d / "zcode.toml").write_text(
            (d / "zcode.toml").read_text().replace('template =', 'delivery = "direct"\ntemplate =', 1)
            + '\n[payload_out.deny]\nexit = 2\n')
        findings = check.run_check(self.repo, ["zcode"])
        self.assertTrue(any(f.severity == "error" and f.code == "mapping-invalid" for f in findings))
