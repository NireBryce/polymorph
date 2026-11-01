"""Tests for the env contract: host facts in, shell exports out, emit-only."""
from __future__ import annotations

import contextlib
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from polymorph import env

ENV = '''schema = 1

[key]
path = "~/.ssh/agents/durandal.key"
scope = "repos/NireBryce/*"

[vault]
unset = ["SSH_AUTH_SOCK", "VAULT_AGENT_SOCKET"]

[harness.zcode]
exports = ["POLYMORPH_HARNESS=zcode", "POLYMORPH_SCOPE=test"]
'''

EXACT = [
    "# polymorph env contract -- harness: zcode",
    "# agent key: ~/.ssh/agents/durandal.key (scope: repos/NireBryce/*)",
    "unset SSH_AUTH_SOCK VAULT_AGENT_SOCKET",
    "export GIT_SSH_COMMAND='ssh -i ~/.ssh/agents/durandal.key -o IdentitiesOnly=yes'",
    "export POLYMORPH_HARNESS=zcode",
    "export POLYMORPH_SCOPE=test",
]


def env_repo(root: Path, text: str = ENV) -> Path:
    (root / ".polymorph/basecamp").mkdir(parents=True, exist_ok=True)
    (root / ".polymorph/basecamp/env.toml").write_text(text)
    return root


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


class EmitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = env_repo(Path(tmp.name))
        self.facts = env.load_env(self.repo)

    def test_exact_emit(self):
        """Slice 2's verification: exact exports from a host-facts fixture."""
        self.assertEqual(env.contract_lines(self.facts, "zcode"), EXACT)

    def test_emit_without_harness_has_no_markers(self):
        lines = env.contract_lines(self.facts)
        self.assertEqual(lines[0], "# polymorph env contract")
        self.assertFalse(any("POLYMORPH_" in l for l in lines))

    def test_emit_without_key_has_no_ssh_line(self):
        facts = env.parse_env({"schema": 1, "vault": {"unset": ["SSH_AUTH_SOCK"]}})
        lines = env.contract_lines(facts)
        self.assertEqual(lines, ["# polymorph env contract", "unset SSH_AUTH_SOCK"])

    def test_exports_are_shell_quoted(self):
        facts = env.parse_env({"schema": 1,
                               "harness": {"x": {"exports": ['PROMPT=hello world; rm -rf /']}}})
        lines = env.contract_lines(facts, "x")
        self.assertEqual(lines[-1], "export PROMPT='hello world; rm -rf /'")

    def test_missing_env_file_names_the_path(self):
        with self.assertRaises(env.EnvError) as ctx:
            env.load_env(self.repo / "elsewhere")
        self.assertIn(".polymorph/basecamp/env.toml", str(ctx.exception))

    def test_unknown_top_key(self):
        with self.assertRaises(env.EnvError):
            env.parse_env({"schema": 1, "extra": 1})

    def test_schema_must_be_one(self):
        with self.assertRaises(env.EnvError):
            env.parse_env({"schema": 2})

    def test_key_needs_path(self):
        with self.assertRaises(env.EnvError):
            env.parse_env({"schema": 1, "key": {"scope": "s"}})

    def test_multiline_value_refused(self):
        with self.assertRaises(env.EnvError):
            env.parse_env({"schema": 1, "key": {"path": "one\ntwo"}})

    def test_unset_names_must_be_env_names(self):
        with self.assertRaises(env.EnvError):
            env.parse_env({"schema": 1, "vault": {"unset": ["not a name"]}})

    def test_exports_must_be_name_value(self):
        with self.assertRaises(env.EnvError):
            env.parse_env({"schema": 1, "harness": {"x": {"exports": ["novalue"]}}})

    def test_harness_table_name_must_be_a_harness_name(self):
        with self.assertRaises(env.EnvError):
            env.parse_env({"schema": 1, "harness": {"not a name": {"exports": []}}})


class ExecTests(unittest.TestCase):
    """--exec runs a real child; the env changes must reach it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = env_repo(Path(tmp.name))

    def run_cli(self, *argv: str, extra_env: dict | None = None) -> tuple[int, str, str]:
        import subprocess
        import sys

        e = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        e.update(extra_env or {})
        p = subprocess.run([sys.executable, "-m", "polymorph", *argv], capture_output=True,
                           text=True, env=e, cwd=self.repo)
        return p.returncode, p.stdout, p.stderr

    def test_exec_applies_unsets_and_markers(self):
        rc, out, err = self.run_cli(
            "env", "--repo", str(self.repo), "--harness", "zcode",
            "--exec", 'test -z "$SSH_AUTH_SOCK" && printf %s "$POLYMORPH_HARNESS"',
            extra_env={"SSH_AUTH_SOCK": "/tmp/agent.sock"})
        self.assertEqual(rc, 0, (out, err))
        self.assertEqual(out, "zcode")

    def test_exec_sets_git_ssh_command(self):
        rc, out, _ = self.run_cli("env", "--repo", str(self.repo),
                                  "--exec", 'printf %s "$GIT_SSH_COMMAND"')
        self.assertEqual(rc, 0)
        self.assertIn("durandal.key", out)
        self.assertIn("IdentitiesOnly=yes", out)

    def test_exec_propagates_exit_code(self):
        rc, _, _ = self.run_cli("env", "--repo", str(self.repo), "--exec", "exit 3")
        self.assertEqual(rc, 3)

    def test_exec_unknown_harness_is_refused(self):
        rc, _, err = self.run_cli("env", "--repo", str(self.repo), "--harness", "nope",
                                  "--exec", "true")
        self.assertEqual(rc, 2)
        self.assertIn("unknown harness 'nope'", err)

    def test_harness_backed_by_a_mapping_alone_is_accepted(self):
        text = ENV.replace('[harness.zcode]\nexports = ["POLYMORPH_HARNESS=zcode", "POLYMORPH_SCOPE=test"]\n', "")
        env_repo(self.repo, text)
        rc, out, err = self.run_cli("env", "--repo", str(self.repo), "--harness", "zcode")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("POLYMORPH_HARNESS", out)  # known via the mapping, no markers declared

    def test_emit_writes_nothing_anywhere(self):
        before = tree_hash(self.repo)
        rc, out, _ = self.run_cli("env", "--repo", str(self.repo), "--harness", "zcode")
        self.assertEqual(rc, 0)
        rc2, _, _ = self.run_cli("env", "--repo", str(self.repo), "--exec", "true")
        self.assertEqual(rc2, 0)
        self.assertEqual(tree_hash(self.repo), before)

    def test_key_contents_are_never_read(self):
        key = self.repo / "durandal.key"
        key.write_text("SECRET-KEY-MATERIAL")
        text = ENV.replace("path = \"~/.ssh/agents/durandal.key\"", f"path = \"{key}\"")
        env_repo(self.repo, text)
        rc, out, _ = self.run_cli("env", "--repo", str(self.repo), "--harness", "zcode",
                                  "--exec", "env")
        self.assertEqual(rc, 0)
        self.assertNotIn("SECRET-KEY-MATERIAL", out)
        self.assertIn("durandal.key", out)  # the path is emitted; the contents never are


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = env_repo(Path(tmp.name))

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        from polymorph import cli

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_cli_emit_text(self):
        rc, out, err = self.run_cli("env", "--repo", str(self.repo), "--harness", "zcode")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.splitlines(), EXACT)

    def test_cli_unknown_harness_exit_2(self):
        rc, _, err = self.run_cli("env", "--repo", str(self.repo), "--harness", "nope")
        self.assertEqual(rc, 2)
        self.assertIn("unknown harness 'nope'", err)

    def test_cli_missing_env_file_exit_2(self):
        rc, _, err = self.run_cli("env", "--repo", str(self.repo.parent))
        self.assertEqual(rc, 2)
        self.assertIn("no env contract at", err)


if __name__ == "__main__":
    unittest.main()
