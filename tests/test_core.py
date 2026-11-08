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

    def test_timeout_stored_unchanged_and_strings_refused(self):
        # a fractional timeout was silently truncated (2.5 -> 2) and a numeric
        # string silently coerced ("30" -> 30); the value must round-trip or fail visibly
        p = self.repo / ".agents/settings.json"
        d = json.loads(p.read_text())
        d["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 2.5
        p.write_text(json.dumps(d))
        self.assertEqual(read_claude(self.repo).hooks[0].timeout, 2.5)
        d["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = 60.0
        p.write_text(json.dumps(d))
        self.assertEqual(read_claude(self.repo).hooks[0].timeout, 60.0)
        for bad in ("30", "0.5", True, 0, -3):
            d["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] = bad
            p.write_text(json.dumps(d))
            with self.subTest(bad=bad):
                with self.assertRaises(ReaderError) as ctx:
                    read_claude(self.repo)
                self.assertIn("timeout", str(ctx.exception))
                # the clear message, not the generic malformed-entry re-wrap
                self.assertNotIn("malformed hooks entry", str(ctx.exception))

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

    def test_script_name_metacharacters_refused(self):
        # each command parses to a single shlex token, so the bare-script rule
        # alone would pass it; the characters reach generated command lines
        p = self.repo / ".agents/settings.json"
        d = json.loads(p.read_text())
        for bad in (
            '"$CLAUDE_PROJECT_DIR/.agents/hooks/x\\"; id; : \\""',
            '"$CLAUDE_PROJECT_DIR/.agents/hooks/$(touch${IFS}/tmp/pwned).sh"',
            '"$CLAUDE_PROJECT_DIR/.agents/hooks/`id`.sh"',
            '".agents/hooks/my hook.sh"',
            '".agents/hooks/$HOME/x.sh"',
        ):
            d["hooks"]["SessionStart"][0]["hooks"][0]["command"] = bad
            p.write_text(json.dumps(d))
            with self.assertRaises(ReaderError, msg=bad):
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
        (self.nobuiltin / "zcode.toml").write_text((self.repo / ".polymorph/basecamp/mappings/zcode.toml").read_text())
        msgs = []
        load_mapping("zcode", self.repo, warn=msgs.append)
        self.assertTrue(any("overrides" in m for m in msgs))

    def test_builtin_fallback_no_warning(self):
        (self.nobuiltin / "other.toml").write_text(
            'schema = 1\nname = "other"\nconfig_path = "a.json"\nformat = "json"\ntemplate = "claude-like"\n'
            '[tools]\n[events]\n[payload_in]\n[payload_out]\n'
            '[capabilities]\nask = false\npost_event = false\nexit_2_blocks = false\n'
            'rewrites_input = false\njson_answers = true\n')
        msgs = []
        self.assertEqual(load_mapping("other", self.repo, warn=msgs.append).name, "other")
        self.assertEqual(msgs, [])

    def test_trust_dir_rule(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        shutil.copy(self.repo / ".polymorph/basecamp/mappings/zcode.toml", outside)
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
            'python3 -m polymorph adapter --harness zcode -- '
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

    def test_undecided_mismatches_write_nothing(self):
        r = generate(self.repo, ["nopost"])[0]
        ids = {u.id for u in r.undecided}
        self.assertIn(".agents/hooks/edit-check-posttooluse.sh:post_tool:no-event", ids)
        self.assertIn(".agents/hooks/secrets-read-guard-pretooluse.sh:pre_tool:no-tool-class", ids)
        self.assertTrue(any("verified" in w for w in r.warnings))
        self.assertIn("attribution", " ".join(r.unmapped))
        self.assertFalse((self.repo / ".nopost/config.json").exists())

    def test_verified_no_warning(self):
        self.assertEqual(generate(self.repo, ["zcode"])[0].warnings, [])

    def test_config_path_symlink_escape_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(outside, self.repo / "escape")
        d = self.repo / ".polymorph/basecamp/mappings"
        text = (d / "zcode.toml").read_text()
        (d / "zcode.toml").write_text(
            text.replace('config_path = ".zcode/config.json"', 'config_path = "escape/evil.json"'))
        with self.assertRaises(ValueError):
            generate(self.repo)
        self.assertFalse((outside / "evil.json").exists())


class CliTests(Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_generate_then_check(self):
        rc, out, _ = self.run_cli("manage", "generate", "--repo", str(self.repo), "--check")
        self.assertEqual(rc, 1)
        self.assertNotIn("warning: sessions already running", out)
        rc, out, _ = self.run_cli("manage", "generate", "--repo", str(self.repo))
        self.assertEqual(rc, 0)
        self.assertIn("unmapped setting: attribution", out)
        self.assertIn("warning: sessions already running will not load the new hooks", out)
        rc, out, _ = self.run_cli("manage", "generate", "--repo", str(self.repo))
        self.assertEqual(rc, 0)
        self.assertIn("unchanged .zcode/config.json", out)
        self.assertIn("warning: sessions already running", out)
        rc, out, _ = self.run_cli("manage", "generate", "--repo", str(self.repo), "--check")
        self.assertEqual(rc, 0)
        self.assertNotIn("warning: sessions already running", out)

    def test_missing_mapping_is_error(self):
        rc, _, err = self.run_cli("manage", "generate", "--repo", str(self.repo), "--harness", "nope")
        self.assertEqual(rc, 2)
        self.assertIn("nope", err)

    def test_adapter_args_after_double_dash(self):
        self.assertTrue(cli._parser().parse_args(["adapter", "--harness", "x", "--", "s.sh"]).script == ["s.sh"])



class SettingsShapeTest(unittest.TestCase):
    def test_claude_settings_template_has_no_wrapper(self):
        from polymorph.generate import _claude_settings, _claude_like
        from polymorph.canon import Canon, Hook
        canon = Canon(hooks=(Hook("pre_tool", ("shell",), ".agents/hooks/x.sh", None),), unmapped=())
        m = load_mapping("claude", Path("/nonexistent-repo"))
        flat = json.loads(_claude_settings(canon, m, {}, {})[0])
        self.assertNotIn("enabled", flat["hooks"])
        group = flat["hooks"]["PreToolUse"][0]
        self.assertEqual(group["matcher"], "Bash")
        self.assertNotIn("enabled", group["hooks"][0])
        wrapped = json.loads(_claude_like(canon, m, {}, {})[0])
        self.assertTrue(wrapped["hooks"]["enabled"])


class DirectDeliveryTests(Base):
    def _write(self, **extra):
        import tomllib
        raw = tomllib.loads((Path(__file__).resolve().parents[1] / "src" / "polymorph" / "mappings" / "zcode.toml").read_text())
        raw.update(extra)
        return raw

    def test_default_is_adapter(self):
        out = generate(self.repo, ["zcode"], check=True)[0].content
        self.assertIn("python3 -m polymorph adapter", out)

    def test_direct_emits_bare_quoted_script(self):
        d = self.repo / ".polymorph/basecamp/mappings"
        text = (d / "zcode.toml").read_text().replace('template =', 'delivery = "direct"\ntemplate =', 1)
        (d / "zcode.toml").write_text(text)
        out = generate(self.repo, ["zcode"], check=True)[0].content
        self.assertNotIn("polymorph adapter", out)
        self.assertIn('"command": "\\"$CLAUDE_PROJECT_DIR/.agents/hooks/session-start.sh\\""', out)

    def test_builtin_zcode_and_claude_are_direct(self):
        mapping.BUILTIN_DIR = Path(mapping.__file__).resolve().parent / "mappings"
        for n in ("zcode", "claude"):
            self.assertEqual(load_mapping(n, self.repo.parent).delivery, "direct")

    def test_direct_delivery_never_asks_missing_field_questions(self):
        # a direct mapping hands the hook the Claude payload unchanged, so a
        # partially-mapped payload_in is not a missing-field mismatch there --
        # while the same trimmed payload_in under adapter delivery is one
        import tomllib
        raw = tomllib.loads(
            (Path(__file__).resolve().parents[1] / "src" / "polymorph" / "mappings" / "zcode.toml").read_text())
        trimmed = dict(raw, payload_in={"tool_name": "tool_name", "cwd": "cwd"})
        d = self.repo / ".polymorph/basecamp/mappings"

        def toml(src: dict) -> str:
            import io

            def quote(v):
                if isinstance(v, bool):
                    return "true" if v else "false"
                if isinstance(v, list):
                    return "[" + ", ".join(quote(x) for x in v) + "]"
                return json.dumps(v)
            buf = io.StringIO()
            for k, v in src.items():
                if isinstance(v, dict):
                    buf.write(f"\n[{k}]\n")
                    for kk, vv in v.items():
                        key = json.dumps(kk) if "." in kk or "-" in kk else kk
                        buf.write(f"{key} = {quote(vv)}\n")
                else:
                    buf.write(f"{k} = {quote(v)}\n")
            return buf.getvalue()

        (d / "pdirect.toml").write_text(toml(dict(trimmed, name="pdirect", delivery="direct")))
        (d / "padapter.toml").write_text(toml(dict(trimmed, name="padapter", delivery="adapter")))
        (self.repo / ".polymorph/basecamp/config.toml").write_text(
            'enabled = ["pdirect", "padapter"]\n')
        rs = {r.harness: r for r in generate(self.repo)}
        self.assertEqual([u.kind for u in rs["pdirect"].undecided], [])
        self.assertIn("missing-field", {u.kind for u in rs["padapter"].undecided})

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

    def test_direct_delivery_requires_canonical_tool_names(self):
        # the harness evaluates the matchers itself: a renamed tool would
        # silently never fire the hook
        from polymorph.validate import validate_mapping
        bad = self._write()
        bad["tools"] = dict(bad["tools"], shell="run_shell")
        errs = validate_mapping(bad)
        self.assertTrue(any("tools.shell" in e for e in errs), errs)
        errs = validate_mapping(self._write(tools={"mcp": "evil__x"}))
        self.assertTrue(any("tools.mcp" in e for e in errs), errs)
        self.assertEqual(validate_mapping(self._write(tools={"mcp": "mcp__x"})), [])

    def test_parse_rejects_direct_non_identity(self):
        raw = self._write(payload_out={"deny": {"exit": 2}})
        with self.assertRaises(MappingError):
            mapping.parse_mapping(raw, "x")

    def test_check_reports_error(self):
        from polymorph import check
        d = self.repo / ".polymorph/basecamp/mappings"
        (d / "zcode.toml").write_text(
            (d / "zcode.toml").read_text().replace('template =', 'delivery = "direct"\ntemplate =', 1)
            + '\n[payload_out.deny]\nexit = 2\n')
        findings = check.run_check(self.repo, ["zcode"])
        self.assertTrue(any(f.severity == "error" and f.code == "mapping-invalid" for f in findings))


class ValidateOutTests(unittest.TestCase):
    """payload_out placeholders and exit values are checked at validation time,
    so a typo or a deny/ask route without an explicit exit = 2 cannot reach the
    adapter and turn a deny into an allow or a non-blocking error."""

    @staticmethod
    def _base(ask_enforced=False, **payload_out):
        # an ask entry only exists on a mapping whose harness enforces ask: with ask = false
        # the hook hardens it and validate rejects the entry (test_conformance)
        return {
            "schema": 1, "name": "x", "config_path": "a.json", "format": "json",
            "template": "t", "tools": {}, "events": {}, "payload_in": {},
            "payload_out": payload_out,
            "capabilities": {"ask": ask_enforced, "post_event": False, "exit_2_blocks": False,
                             "rewrites_input": False, "json_answers": True},
        }

    def test_known_placeholders_valid(self):
        from polymorph.validate import validate_mapping
        m = self._base(deny={"exit": 2, "stderr": "{reason}"},
                       warn={"exit": 0, "stdout": "{context}"})
        self.assertEqual(validate_mapping(m), [])

    def test_unknown_placeholder_is_an_error(self):
        from polymorph.validate import validate_mapping
        errs = validate_mapping(self._base(deny={"exit": 2, "stderr": "{reson}"}))
        self.assertTrue(any("reson" in e for e in errs), errs)

    def test_deny_or_ask_exit_zero_is_an_error(self):
        from polymorph.validate import validate_mapping
        for kind in ("deny", "ask"):
            errs = validate_mapping(self._base(ask_enforced=kind == "ask", **{kind: {"exit": 0}}))
            self.assertTrue(any(f"{kind}.exit" in e for e in errs), errs)
        for kind in ("allow", "warn", "remind"):
            self.assertEqual(validate_mapping(self._base(**{kind: {"exit": 0}})), [])

    def test_deny_or_ask_exit_must_be_the_canonical_block_code(self):
        # the adapter defaults a missing exit to 0, and no code but 2 is documented
        # to block: a deny or ask rendered as anything else fails open silently
        from polymorph.validate import validate_mapping
        for kind in ("deny", "ask"):
            for spec in ({}, {"stdout": "x"}, {"stderr": "x"}, {"exit": 1}, {"exit": 3}):
                errs = validate_mapping(self._base(ask_enforced=kind == "ask", **{kind: spec}))
                self.assertTrue(any(f"{kind}.exit" in e for e in errs), (kind, spec, errs))
        self.assertEqual(validate_mapping(self._base(deny={"exit": 2})), [])
        self.assertEqual(validate_mapping(self._base(ask_enforced=True,
                                                     ask={"exit": 2, "stderr": "{reason}"})), [])


class HookCommandTimeoutTests(unittest.TestCase):
    def test_adapter_command_carries_the_hook_timeout(self):
        from polymorph import generate, mapping as mapping_mod

        m = mapping_mod.load_mapping("gemini")   # adapter delivery
        self.assertIn("--timeout 300", generate.hook_command(m, "s.sh", 300))
        self.assertNotIn("--timeout", generate.hook_command(m, "s.sh"))
        direct = mapping_mod.load_mapping("zcode")   # direct delivery: the harness owns the timeout
        self.assertNotIn("--timeout", generate.hook_command(direct, "s.sh", 300))


class FailOpenConfigTests(unittest.TestCase):
    def test_fail_open_flag_is_in_the_adapter_command_only_when_asked(self):
        from polymorph import generate, mapping as mapping_mod

        gemini = mapping_mod.load_mapping("gemini")
        self.assertNotIn("--fail-open", generate.hook_command(gemini, "s.sh"))
        self.assertIn(" --fail-open ", generate.hook_command(gemini, "s.sh", 30, fail_open=True))
        zcode = mapping_mod.load_mapping("zcode")   # direct: no adapter runs, nothing to flag
        self.assertNotIn("--fail-open", generate.hook_command(zcode, "s.sh", fail_open=True))

    def test_config_reads_fail_open_and_still_rejects_other_keys(self):
        from polymorph import generate

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".polymorph/basecamp").mkdir(parents=True)
            config = repo / ".polymorph/basecamp/config.toml"
            config.write_text('enabled = ["gemini"]\nfail_open = ["gemini"]\n')
            self.assertEqual(generate.fail_open_targets(repo), ["gemini"])
            self.assertEqual(generate.enabled_targets(repo), ["gemini"])
            config.write_text('enabled = ["gemini"]\nfail-open = ["gemini"]\n')
            with self.assertRaises(ValueError):
                generate.fail_open_targets(repo)


if __name__ == "__main__":
    unittest.main()
