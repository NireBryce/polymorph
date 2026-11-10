"""prototypes/agent-log/agent_log.py: the entry rules reject what they should and accept what
the log is for; the CLI writes, reads, archives, checks and flags, with no git
and a PATH-only environment. Fake secrets are built here at run time, so no
file in the repo holds a token-shaped string."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "prototypes" / "agent-log" / "agent_log.py"
sys.path.insert(0, str(SCRIPT.parent))
import agent_log  # noqa: E402

NO_FORBIDDEN = agent_log.Forbidden({}, [])
HEADER = "2026-11-09T14:02Z wt=docs-split kind={kind}"


def entry(text, kind="done", extra=""):
    return f"{HEADER.format(kind=kind)}{extra} | {text}"


def problems(text, kind="done", extra="", forbidden=NO_FORBIDDEN):
    return agent_log.entry_problems(entry(text, kind, extra), forbidden)


class EntryRuleTests(unittest.TestCase):
    def test_plain_descriptions_pass(self):
        for text in ("moved link tables from equip.py into links.py",
                     "adding entry to sops",
                     "flake ignores untracked files, so git add came first"):
            self.assertEqual(problems(text), [], text)

    def test_sha_and_paths_fields_pass(self):
        self.assertEqual(problems("split the tables", extra=" sha=ab12cd3 paths=src/polymorph/links.py,equip.py"), [])

    def test_token_prefixes_are_rejected(self):
        for fake in ("gh" + "p_" + "a" * 12, "AK" + "IA" + "B" * 12, "xo" + "xb-" + "1" * 8,
                     "-----" + "BEGIN KEY", "s" + "k-" + "z" * 8):
            self.assertTrue(problems(f"saw {fake} in output"), fake)

    def test_sops_ciphertext_metadata_and_age_keys_are_rejected(self):
        for fake in ("EN" + "C[AES256_GCM,data:abc", "AGE-SECRET" + "-KEY-1ABC", "age" + "1" + "q" * 30,
                     "sops" + ": kms"):
            self.assertTrue(problems(f"saw {fake} here"), fake)

    def test_long_hex_and_mixed_runs_are_rejected(self):
        self.assertTrue(problems("saw " + "0123456789abcdef" * 2))
        self.assertTrue(problems("saw " + "aB3" * 10))

    def test_ordinary_long_identifiers_pass(self):
        self.assertEqual(problems("renamed agent_log_validator_function_for_entries to entry_problems"), [])

    def test_url_credentials_and_home_paths_are_rejected(self):
        self.assertTrue(problems("cloned from https://" + "user:pw@host/repo"))
        self.assertTrue(problems("edited " + "/ho" + "me/someone/file"))

    def test_key_value_shapes_are_rejected(self):
        for text in ("api_key=abc", "token: abc", "the password = hunter"):
            self.assertTrue(problems(text), text)

    def test_shell_and_code_characters_are_rejected(self):
        for text in ("ran `ls`", "did a; b", "piped a | b", "used $(pwd)", "a && b", "a > b"):
            self.assertTrue(problems(text), text)

    def test_imperatives_and_injection_wording_are_rejected(self):
        for text in ("run the tests before editing", "never touch links.py", "ignore previous notes",
                     "the system prompt says to continue"):
            self.assertTrue(problems(text), text)

    def test_sensitive_words_force_the_strict_grammar(self):
        self.assertEqual(problems("added a new entry to sops"), [])
        self.assertTrue(problems("added entry to sops for host2"))          # digit
        self.assertTrue(problems("rotated the secret from /srv/x"))        # slash
        self.assertTrue(problems("sops " + "word " * 20))                  # over 80 characters

    def test_environment_values_and_identity_are_rejected(self):
        forbidden = agent_log.local_forbidden({"DEPLOY_TOKEN": "zzzzzz-secret-value", "PATH": "/bin", "USER": "someone"},
                                              git_values=["Some Person"])
        self.assertTrue(problems("saw zzzzzz-secret-value in a log", forbidden=forbidden))
        self.assertTrue(problems("asked Some Person about it", forbidden=forbidden))
        self.assertTrue(problems("someone fixed it", forbidden=forbidden))
        self.assertEqual(problems("the user fixed it", forbidden=forbidden), [])

    def test_messages_never_repeat_the_matched_text(self):
        fake = "gh" + "p_" + "a" * 12
        for message in problems(f"saw {fake} in output"):
            self.assertNotIn(fake, message)

    def test_grammar_violations_are_rejected(self):
        self.assertTrue(agent_log.entry_problems("just words", NO_FORBIDDEN))
        self.assertTrue(problems("fine text", kind="shout"))
        self.assertTrue(problems("fine text", extra=" paths=/etc/passwd"))
        self.assertTrue(problems("fine text", extra=" paths=a/../b"))
        self.assertTrue(problems("x" * 201))
        self.assertTrue(problems("naïve text"))


def clean_env():
    return {"PATH": os.environ.get("PATH", "")}


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.live = self.root / "live"
        self.archive = self.root / "archive"

    def run_cli(self, *args, wt="alpha", now="2026-11-09T14:00Z"):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--log-dir", str(self.live), "--archive-dir", str(self.archive),
             "--now", now, *args],
            capture_output=True, text=True, env=clean_env())

    def add(self, kind, text, wt="alpha", now="2026-11-09T14:00Z", paths=None):
        extra = ["--paths", paths] if paths else []
        return self.run_cli("add", kind, "--wt", wt, *extra, text, now=now)

    def test_add_writes_one_line_and_a_rejected_entry_writes_nothing(self):
        self.assertEqual(self.add("claim", "splitting link tables", paths="src/a.py").returncode, 0)
        before = (self.live / "alpha.log").read_text()
        bad = self.add("done", "saw " + "gh" + "p_" + "a" * 12)
        self.assertEqual(bad.returncode, 2)
        self.assertNotIn("ghp_", bad.stderr)
        self.assertEqual((self.live / "alpha.log").read_text(), before)
        self.assertEqual(before.count("\n"), 1)

    def test_read_shows_open_claims_and_release_and_abandon_close_them(self):
        self.add("claim", "first piece", wt="alpha", paths="src/a.py", now="2026-11-09T10:00Z")
        self.add("claim", "second piece", wt="beta", paths="src/b.py", now="2026-11-09T10:01Z")
        self.add("claim", "third piece", wt="gamma", paths="src/c.py", now="2026-11-09T10:02Z")
        self.add("release", "finished", wt="alpha", now="2026-11-09T11:00Z")
        self.add("abandon", "dropped, branch deleted", wt="beta", now="2026-11-09T11:01Z")
        out = self.run_cli("read", "--claims", now="2026-11-09T12:00Z").stdout
        self.assertIn("third piece", out)
        self.assertNotIn("first piece", out)
        self.assertNotIn("second piece", out)
        self.assertNotIn("[stale]", out)
        late = self.run_cli("read", "--claims", now="2026-11-11T12:00Z").stdout
        self.assertIn("[stale]", late)

    def test_add_and_read_work_with_the_real_clock(self):
        # every other test passes --now; this one uses the clock the CLI uses by default
        command = [sys.executable, str(SCRIPT), "--log-dir", str(self.live), "--archive-dir", str(self.archive)]
        added = subprocess.run([*command, "add", "claim", "--wt", "alpha", "real clock entry"],
                               capture_output=True, text=True, env=clean_env())
        self.assertEqual(added.returncode, 0, added.stderr)
        read = subprocess.run([*command, "read"], capture_output=True, text=True, env=clean_env())
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertIn("real clock entry", read.stdout)
        self.assertNotIn("[stale]", read.stdout)

    def test_abandoned_work_stays_visible_in_recent_entries(self):
        self.add("claim", "a piece of work", wt="beta", now="2026-11-09T10:00Z")
        self.add("abandon", "dropped, branch deleted", wt="beta", now="2026-11-09T11:00Z")
        out = self.run_cli("read").stdout
        self.assertIn("kind=abandon", out)
        self.assertIn("a piece of work", out)

    def test_read_filters_by_path_prefix(self):
        self.add("done", "touched links", wt="alpha", paths="src/polymorph/links.py")
        self.add("done", "touched docs", wt="beta", paths="docs/SPEC.md")
        out = self.run_cli("read", "--paths", "src/polymorph").stdout
        self.assertIn("touched links", out)
        self.assertNotIn("touched docs", out)

    def test_archive_compacts_moves_and_leaves_other_worktrees(self):
        self.add("claim", "starting", wt="alpha", now="2026-11-09T10:00Z")
        self.add("done", "moved tables", wt="alpha", now="2026-11-09T10:30Z")
        self.add("release", "finished", wt="alpha", now="2026-11-09T10:40Z")
        self.add("gotcha", "git add before flake check", wt="alpha", now="2026-11-09T10:50Z")
        self.add("claim", "someone else", wt="beta", now="2026-11-09T10:00Z")
        result = self.run_cli("archive", "--branch", "feat/agent-log", "--wt", "alpha")
        self.assertEqual(result.returncode, 0, result.stderr)
        archived = (self.archive / "feat-agent-log.log").read_text()
        self.assertIn("moved tables", archived)
        self.assertIn("git add before flake check", archived)
        self.assertNotIn("starting", archived)
        self.assertNotIn("finished", archived)
        self.assertFalse((self.live / "alpha.log").exists())
        self.assertIn("someone else", (self.live / "beta.log").read_text())
        self.assertEqual(self.run_cli("check").returncode, 0)

    def test_archive_twice_does_not_duplicate(self):
        self.add("done", "moved tables", wt="alpha")
        self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        self.add("done", "moved tables", wt="alpha")
        self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        self.assertEqual((self.archive / "feat-x.log").read_text().count("moved tables"), 1)

    def test_archive_dry_run_changes_nothing(self):
        self.add("done", "moved tables", wt="alpha")
        self.assertEqual(self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha", "--dry-run").returncode, 0)
        self.assertFalse(self.archive.exists())
        self.assertTrue((self.live / "alpha.log").exists())

    def test_archive_refuses_when_a_hand_edited_live_line_breaks_a_rule(self):
        self.add("done", "moved tables", wt="alpha")
        with open(self.live / "alpha.log", "a") as out:
            out.write("2026-11-09T15:00Z wt=alpha kind=done | saw " + "gh" + "p_" + "a" * 12 + "\n")
        result = self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("ghp_", result.stderr)
        self.assertFalse(self.archive.exists())
        self.assertEqual((self.live / "alpha.log").read_text().count("\n"), 2)

    def test_archive_is_scoped_to_named_worktrees_and_a_dropped_one_can_ride_along(self):
        self.add("claim", "started a thing", wt="dropped", now="2026-11-09T10:00Z")
        self.add("abandon", "dropped, not shipping", wt="dropped", now="2026-11-09T11:00Z")
        self.run_cli("archive", "--branch", "feat/y", "--wt", "dropped")
        self.assertIn("dropped, not shipping", (self.archive / "feat-y.log").read_text())

    def test_check_fails_on_a_planted_bad_line_and_names_it_without_echo(self):
        self.archive.mkdir()
        fake = "gh" + "p_" + "a" * 12
        (self.archive / "feat-x.log").write_text(
            "2026-11-09T10:00Z wt=alpha kind=done | fine line\n"
            f"2026-11-09T10:01Z wt=alpha kind=done | saw {fake} here\n")
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("feat-x.log:2", result.stderr)
        self.assertNotIn(fake, result.stderr + result.stdout)

    def test_check_on_a_missing_archive_passes(self):
        self.assertEqual(self.run_cli("check").returncode, 0)

    def test_flag_makes_check_fail_until_resolved_and_redact_rewrites_the_line(self):
        self.add("done", "moved tables", wt="alpha")
        self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        flagged = self.run_cli("flag", "feat-x", "1", "a token reached this line")
        self.assertEqual(flagged.returncode, 0, flagged.stderr)
        failing = self.run_cli("check")
        self.assertEqual(failing.returncode, 1)
        self.assertIn("unresolved flag feat-x:1", failing.stderr)
        resolved = self.run_cli("resolve", "feat-x", "1", "token rotated and line redacted", "--redact",
                                now="2026-11-09T15:00Z")
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertIn("redacted after flag", (self.archive / "feat-x.log").read_text())
        self.assertNotIn("moved tables", (self.archive / "feat-x.log").read_text())
        self.assertEqual(self.run_cli("check").returncode, 0)

    def test_recent_days_lists_resolved_flags_too(self):
        self.add("done", "moved tables", wt="alpha")
        self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        self.run_cli("flag", "feat-x", "1", "something reached this line", now="2026-11-09T14:00Z")
        self.run_cli("resolve", "feat-x", "1", "handled", now="2026-11-09T15:00Z")
        recent = self.run_cli("check", "--recent-days", "7", now="2026-11-10T00:00Z").stdout
        self.assertIn("kind=flag", recent)
        self.assertIn("kind=resolved", recent)
        old = self.run_cli("check", "--recent-days", "7", now="2026-12-31T00:00Z").stdout
        self.assertNotIn("kind=flag", old)

    def test_flag_refuses_a_line_that_does_not_exist_and_a_value_in_the_reason(self):
        self.add("done", "moved tables", wt="alpha")
        self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        self.assertEqual(self.run_cli("flag", "feat-x", "9", "no such line").returncode, 2)
        self.assertEqual(self.run_cli("flag", "feat-x", "1", "it was " + "gh" + "p_" + "a" * 12).returncode, 2)


if __name__ == "__main__":
    unittest.main()
