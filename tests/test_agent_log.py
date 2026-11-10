"""prototypes/agent-log/agent_log.py: each entry rule fires on its own input (each
assertion names the rule's message, so a removed rule fails its test and is not
caught by a neighbour), the CLI writes, reads, archives, prunes, checks and flags
with no git and a PATH-only environment, and the bugs a review found stay fixed.
Fake secrets are built here at run time, so no file in the repo holds a
token-shaped string."""
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
TS = "2026-11-09T14:02Z"


def make_line(kind="done", text=None, wt="docs-split", ts=TS, ref=None, verb=None, sha=None, paths=None):
    """A well-formed entry with its real id, so only the field under test can be wrong."""
    body = f"{ts} wt={wt} kind={kind}"
    for field, value in (("ref", ref), ("verb", verb), ("sha", sha), ("paths", paths)):
        if value:
            body += f" {field}={value}"
    if text is not None:
        body += f" | {text}"
    return body.replace(f" wt={wt}", f" id={agent_log.entry_id(body)} wt={wt}", 1)


def problems(text="moved the tables", forbidden=NO_FORBIDDEN, **fields):
    return agent_log.entry_problems(make_line(text=text, **fields), forbidden)


class EntryRuleTests(unittest.TestCase):
    def assertRule(self, found, message_part):
        self.assertTrue(any(message_part in message for message in found), f"{message_part!r} not in {found}")

    def test_plain_descriptions_and_every_field_pass(self):
        for text in ("moved link tables from equip.py into links.py", "flake ignores untracked files, so git add came first",
                     "moved X -> Y; tests pass", "use-a-worktree skill now claims paths", "open claims now sorted",
                     "renamed agent_log_validator_function_for_entries to entry_problems"):
            self.assertEqual(problems(text), [], text)
        self.assertEqual(problems(sha="ab12cd3", paths="src/polymorph/links.py,equip.py"), [])
        self.assertEqual(problems(kind="release", ref="0123456789", text="finished"), [])

    def test_token_prefixes(self):
        for fake in ("gh" + "p_" + "a" * 12, "AK" + "IA" + "B" * 12, "xo" + "xb-" + "1" * 8, "xo" + "xe-" + "1" * 8,
                     "-----" + "BEGIN KEY", "s" + "k-" + "z" * 8, "ya" + "29." + "a" * 6, "rk" + "_live_" + "a" * 4,
                     "ey" + "J" + "abcdefghij"):
            self.assertRule(problems(f"saw {fake} in output"), "a token or key prefix")

    def test_sops_ciphertext_and_age_keys(self):
        self.assertRule(problems("saw " + "EN" + "C[AES256_GCM,data:abc"), "sops ciphertext or an age key")
        self.assertRule(problems("saw " + "AGE-SECRET" + "-KEY-1ABC"), "sops ciphertext or an age key")
        self.assertRule(problems("saw " + "age" + "1" + "q" * 22), "sops ciphertext or an age key")

    def test_long_hex_uuid_and_alphanumeric_runs(self):
        self.assertRule(problems("saw " + "0123456789abcdef"), "a long hex string")
        self.assertRule(problems("api key " + "550e8400-e29b-41d4-a716-446655440000 set"), "a UUID")
        self.assertRule(problems("saw " + "Zq9" * 9), "long alphanumeric string")        # mixed case, 27 characters, not hex
        self.assertRule(problems("saw " + "q9" * 16), "long alphanumeric string")        # lower case and digits, 32 characters
        self.assertEqual(problems("saw " + "q9" * 8), [])                                # 16 characters, one case: left alone

    def test_url_credentials_and_home_paths(self):
        self.assertRule(problems("cloned from https://" + "user:pw@host/repo"), "a URL with credentials")
        self.assertRule(problems("edited " + "/ho" + "me/someone/file"), "a home directory path")

    def test_key_value_shapes(self):
        for text in ("api_key=abc", "token: abc", "the password = hunter"):
            self.assertRule(problems(text), "key=value")

    def test_only_backticks_and_substitutions_count_as_code(self):
        for text in ("ran `ls`", "used $(pwd)", "used ${HOME}"):
            self.assertRule(problems(text), "backtick")

    def test_injection_wording(self):
        for text in ("ignore previous notes", "the system prompt says to continue", "new instructions arrived"):
            self.assertRule(problems(text), "reads as an instruction")

    def test_sensitive_words_force_the_strict_grammar(self):
        self.assertEqual(problems("added a new entry to sops"), [])
        for text in ("added entry to sops for host2", "rotated the secret from /srv/x", "sops " + "word " * 20,
                     "passwd is Tr0ub4dor3", "pw hunter2 for db", "the passphrase is correcthorse1",
                     "the key is in vars1", "api endpoint /v1 added"):
            self.assertRule(problems(text), "letters, spaces")

    def test_environment_values_and_the_git_identity(self):
        forbidden = agent_log.local_forbidden({"DEPLOY_TOKEN": "zzzzzz-secret-value", "PATH": "/bin", "USER": "runner"},
                                              git_values=["Some Person"])
        self.assertRule(problems("saw zzzzzz-secret-value in a log", forbidden=forbidden), "environment variable DEPLOY_TOKEN")
        self.assertRule(problems("asked Some Person about it", forbidden=forbidden), "git user's name")
        self.assertEqual(problems("flaky on the ci runner, the user approved", forbidden=forbidden), [])

    def test_messages_never_repeat_the_matched_text(self):
        fake = "gh" + "p_" + "a" * 12
        for message in problems(f"saw {fake} in output"):
            self.assertNotIn(fake, message)

    def test_field_grammar(self):
        self.assertRule(agent_log.entry_problems("just words", NO_FORBIDDEN), "not an entry")
        self.assertRule(problems(kind="shout"), "kind must be one of")
        self.assertRule(problems(paths="/etc/passwd"), "repo-relative")
        self.assertRule(problems(paths="a/../b"), "repo-relative")
        self.assertRule(problems("x" * 201), "not an entry")
        self.assertRule(problems("naïve text"), "not an entry")
        self.assertRule(problems(ts="2026-13-45T10:00Z"), "not a real date")
        self.assertRule(problems(kind="redacted"), "only written by resolve --redact")
        self.assertRule(problems(ref="0123456789"), "release and abandon entries only")
        self.assertRule(problems(verb="added"), "belongs to sops entries only")
        self.assertRule(agent_log.entry_problems(make_line(text=None), NO_FORBIDDEN), "needs text")

    def test_the_id_must_match_the_line(self):
        tampered = make_line(text="moved the tables").replace("moved", "removed")
        self.assertRule(agent_log.entry_problems(tampered, NO_FORBIDDEN), "id does not match")

    def test_sops_entries_have_a_verb_and_paths_and_no_text(self):
        self.assertEqual(problems(kind="sops", text=None, verb="added", paths="secrets/prod.yaml"), [])
        self.assertRule(problems(kind="sops", text=None, paths="secrets/prod.yaml"), "needs verb=")
        self.assertRule(problems(kind="sops", text=None, verb="added"), "needs paths=")
        self.assertRule(problems(kind="sops", text="added entry", verb="added", paths="secrets/prod.yaml"), "no text")
        self.assertRule(problems(kind="sops", text=None, verb="decrypted", paths="a"), "needs verb=")


def clean_env():
    return {"PATH": os.environ.get("PATH", "")}


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.live = self.root / "live"
        self.repo = self.root / "repo"
        self.archive = self.repo / "wiki" / "agent-log"
        for name in ("src/a.py", "src/b.py", "secrets/prod.yaml"):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / name).write_text("x\n")

    def run_cli(self, *args, now="2026-11-09T14:00Z"):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--log-dir", str(self.live), "--archive-dir", str(self.archive),
             "--repo-root", str(self.repo), "--now", now, *args],
            capture_output=True, text=True, env=clean_env())

    def add(self, kind, *text, wt="alpha", now="2026-11-09T14:00Z", paths=None, extra=()):
        paths_args = ["--paths", paths] if paths else []
        return self.run_cli("add", kind, "--wt", wt, *paths_args, *extra, *text, now=now)

    def add_id(self, *args, **kwargs):
        result = self.add(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def live_bytes(self, wt="alpha"):
        return (self.live / f"{wt}.log").read_bytes()

    # --- add

    def test_add_prints_the_id_and_a_rejected_entry_writes_nothing(self):
        entry = self.add_id("claim", "splitting link tables", paths="src/a.py")
        self.assertRegex(entry, "^[0-9a-f]{10}$")
        before = self.live_bytes()
        bad = self.add("done", "saw " + "gh" + "p_" + "a" * 12)
        self.assertEqual(bad.returncode, 2)
        self.assertNotIn("ghp_", bad.stderr)
        self.assertEqual(self.live_bytes(), before)

    def test_entries_that_will_be_committed_must_name_paths_that_exist_but_claims_may_name_new_files(self):
        self.assertEqual(self.add("claim", "creating a file", paths="src/new_file.py").returncode, 0)
        for kind in ("decision", "gotcha"):
            result = self.add(kind, "a thing happened", paths="Tr0ub4dor3")
            self.assertEqual(result.returncode, 2, kind)
            self.assertIn("must exist in the repo", result.stderr)
        self.assertEqual(self.add("decision", "chose a layout", paths="src/a.py").returncode, 0)

    def test_sops_entry_takes_a_verb_and_a_path_and_no_text(self):
        ok = self.add("sops", paths="secrets/prod.yaml", extra=("--verb", "added"))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn("kind=sops verb=added paths=secrets/prod.yaml", self.live_bytes().decode())
        self.assertEqual(self.add("sops", "added a value", paths="secrets/prod.yaml", extra=("--verb", "added")).returncode, 2)
        self.assertEqual(self.add("sops", paths="secrets/prod.yaml").returncode, 2)

    def test_add_and_read_work_with_the_real_clock(self):
        # every other test passes --now; this one uses the clock the CLI uses by default
        command = [sys.executable, str(SCRIPT), "--log-dir", str(self.live), "--archive-dir", str(self.archive),
                   "--repo-root", str(self.repo)]
        added = subprocess.run([*command, "add", "claim", "--wt", "alpha", "real clock entry"],
                               capture_output=True, text=True, env=clean_env())
        self.assertEqual(added.returncode, 0, added.stderr)
        read = subprocess.run([*command, "read"], capture_output=True, text=True, env=clean_env())
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertIn("real clock entry", read.stdout)
        self.assertNotIn("[stale]", read.stdout)

    def test_a_bad_now_is_a_usage_error(self):
        self.assertEqual(self.add("done", "text", now="yesterday").returncode, 2)

    # --- read

    def test_release_with_a_ref_closes_one_claim_and_without_one_closes_all_of_the_worktrees(self):
        first = self.add_id("claim", "first piece", wt="alpha", paths="src/a.py", now="2026-11-09T10:00Z")
        self.add_id("claim", "second piece", wt="alpha", paths="src/b.py", now="2026-11-09T10:01Z")
        self.add_id("claim", "other worktree", wt="beta", paths="src/c.py", now="2026-11-09T10:02Z")
        self.add_id("release", "finished the first", wt="alpha", extra=("--ref", first), now="2026-11-09T11:00Z")
        out = self.run_cli("read", "--claims", now="2026-11-09T12:00Z").stdout
        self.assertNotIn("first piece", out)
        self.assertIn("second piece", out)
        self.assertIn("other worktree", out)
        self.add_id("release", "finished", wt="alpha", now="2026-11-09T11:30Z")
        out = self.run_cli("read", "--claims", now="2026-11-09T12:00Z").stdout
        self.assertNotIn("second piece", out)
        self.assertIn("other worktree", out)

    def test_abandon_closes_claims_and_stays_visible(self):
        self.add_id("claim", "a piece of work", wt="beta", now="2026-11-09T10:00Z")
        self.add_id("abandon", "dropped, branch deleted", wt="beta", now="2026-11-09T11:00Z")
        out = self.run_cli("read", now="2026-11-09T12:00Z").stdout
        self.assertIn("open claims: none", out)
        self.assertIn("kind=abandon", out)
        self.assertIn("a piece of work", out)

    def test_stale_claims_are_marked(self):
        self.add_id("claim", "an old claim", now="2026-11-09T10:00Z")
        self.assertNotIn("[stale]", self.run_cli("read", "--claims", now="2026-11-09T12:00Z").stdout)
        self.assertIn("[stale]", self.run_cli("read", "--claims", now="2026-11-11T12:00Z").stdout)

    def test_read_filters_by_path_prefix_and_says_how_many_older_entries_it_left_out(self):
        self.add_id("done", "touched a", wt="alpha", paths="src/a.py", now="2026-11-09T10:00Z")
        self.add_id("done", "touched docs", wt="beta", paths="secrets/prod.yaml", now="2026-11-09T10:01Z")
        self.add_id("done", "touched b", wt="gamma", paths="src/b.py", now="2026-11-09T10:02Z")
        out = self.run_cli("read", "--paths", "src").stdout
        self.assertIn("touched a", out)
        self.assertNotIn("touched docs", out)
        short = self.run_cli("read", "--tail", "1").stdout
        self.assertIn("2 older not shown", short)
        self.assertIn("touched b", short)

    def test_a_hand_written_bad_timestamp_does_not_crash_read(self):
        self.add_id("done", "a fine entry")
        broken = "2026-13-45T10:00Z id=0123456789 wt=alpha kind=done | broken stamp"
        with open(self.live / "alpha.log", "a") as out:
            out.write(broken + "\n")
        result = self.run_cli("read")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("a fine entry", result.stdout)
        self.assertNotIn("broken stamp", result.stdout)

    def test_read_hides_entries_already_archived_unless_asked(self):
        self.add_id("decision", "chose a layout", paths="src/a.py")
        self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        self.assertNotIn("chose a layout", self.run_cli("read").stdout)
        self.assertIn("chose a layout", self.run_cli("read", "--archive").stdout)

    # --- archive

    def test_archive_copies_only_the_kinds_worth_keeping_and_never_touches_the_live_log(self):
        self.add_id("claim", "starting", now="2026-11-09T10:00Z")
        self.add_id("done", "moved tables", now="2026-11-09T10:30Z")
        self.add_id("decision", "chose a split", now="2026-11-09T10:40Z")
        self.add_id("gotcha", "git add before flake check", now="2026-11-09T10:50Z")
        self.add_id("sops", wt="alpha", paths="secrets/prod.yaml", extra=("--verb", "rotated"), now="2026-11-09T10:55Z")
        before = self.live_bytes()
        result = self.run_cli("archive", "--branch", "feat/agent-log", "--wt", "alpha")
        self.assertEqual(result.returncode, 0, result.stderr)
        archived = (self.archive / "feat-agent-log.log").read_text()
        for kept in ("chose a split", "git add before flake check", "kind=sops verb=rotated"):
            self.assertIn(kept, archived)
        for left in ("starting", "moved tables"):
            self.assertNotIn(left, archived)
        self.assertEqual(self.live_bytes(), before)
        self.assertEqual(self.run_cli("check").returncode, 0)

    def test_archiving_twice_adds_nothing_and_a_later_archive_only_appends(self):
        self.add_id("decision", "second decision", now="2026-11-09T12:00Z")
        self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        first = (self.archive / "feat-x.log").read_text()
        self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        self.assertEqual((self.archive / "feat-x.log").read_text(), first)
        self.add_id("decision", "an earlier decision by another worktree", wt="beta", now="2026-11-09T10:00Z")
        self.run_cli("archive", "--branch", "feat/x", "--wt", "beta")
        grown = (self.archive / "feat-x.log").read_text()
        self.assertTrue(grown.startswith(first))
        self.assertEqual(grown.count("\n"), 2)

    def test_archive_dry_run_changes_nothing(self):
        self.add_id("decision", "chose a split")
        self.assertEqual(self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha", "--dry-run").returncode, 0)
        self.assertFalse(self.archive.exists())

    def test_archive_refuses_when_a_hand_edited_live_line_breaks_a_rule(self):
        self.add_id("decision", "chose a split")
        fake = "gh" + "p_" + "a" * 12
        bad_body = f"2026-11-09T15:00Z wt=alpha kind=gotcha | saw {fake}"
        with open(self.live / "alpha.log", "a") as out:
            out.write(bad_body.replace(" wt=alpha", f" id={agent_log.entry_id(bad_body)} wt=alpha", 1) + "\n")
        result = self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(fake, result.stderr)
        self.assertFalse(self.archive.exists())

    def test_archive_refuses_a_path_that_does_not_exist(self):
        body = "2026-11-09T15:00Z wt=alpha kind=gotcha paths=Tr0ub4dor3 | hand written"
        self.live.mkdir()
        (self.live / "alpha.log").write_text(body.replace(" wt=alpha", f" id={agent_log.entry_id(body)} wt=alpha", 1) + "\n")
        self.assertEqual(self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha").returncode, 1)
        self.assertFalse(self.archive.exists())

    def test_a_dropped_worktree_can_be_archived_with_another_branch(self):
        self.add_id("claim", "started a thing", wt="dropped", now="2026-11-09T10:00Z")
        self.add_id("abandon", "dropped, not shipping", wt="dropped", now="2026-11-09T11:00Z")
        self.run_cli("archive", "--branch", "feat/y", "--wt", "dropped")
        self.assertIn("dropped, not shipping", (self.archive / "feat-y.log").read_text())

    # --- prune

    def test_prune_removes_a_live_log_only_when_its_archivable_entries_are_archived(self):
        self.add_id("done", "not archived by design")
        decision = self.add_id("decision", "chose a split")
        refused = self.run_cli("prune", "--wt", "alpha")
        self.assertEqual(refused.returncode, 1)
        self.assertTrue((self.live / "alpha.log").exists())
        self.assertIn(decision, refused.stderr)
        self.run_cli("archive", "--branch", "feat/x", "--wt", "alpha")
        self.assertEqual(self.run_cli("prune", "--wt", "alpha").returncode, 0)
        self.assertFalse((self.live / "alpha.log").exists())

    def test_prune_names_a_worktree_with_no_log(self):
        self.assertEqual(self.run_cli("prune", "--wt", "nobody").returncode, 1)

    # --- check

    def test_check_fails_on_a_planted_bad_line_and_names_it_without_echo(self):
        self.archive.mkdir(parents=True)
        fake = "gh" + "p_" + "a" * 12
        good = make_line(kind="gotcha", text="fine line")
        bad = make_line(kind="gotcha", text=f"saw {fake} here")
        (self.archive / "feat-x.log").write_text(f"{good}\n\n{bad}\n")
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("feat-x.log:3", result.stderr)
        self.assertNotIn(fake, result.stderr + result.stdout)

    def test_check_fails_on_an_edited_line(self):
        self.archive.mkdir(parents=True)
        edited = make_line(kind="gotcha", text="fine line").replace("fine", "edited")
        (self.archive / "feat-x.log").write_text(edited + "\n")
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("id does not match", result.stderr)

    def test_check_on_a_missing_archive_passes_and_ignores_blank_lines(self):
        self.assertEqual(self.run_cli("check").returncode, 0)
        self.archive.mkdir(parents=True)
        (self.archive / "feat-x.log").write_text("\n" + make_line(kind="gotcha", text="fine line") + "\n\n")
        self.assertEqual(self.run_cli("check").returncode, 0)

    # --- flag and resolve

    def archived_entry(self, text="chose a split", kind="decision", wt="alpha", now="2026-11-09T12:00Z", branch="feat/x"):
        entry = self.add_id(kind, text, wt=wt, now=now)
        self.run_cli("archive", "--branch", branch, "--wt", wt)
        return entry

    def test_flag_makes_check_fail_until_resolved_and_redact_replaces_the_entry(self):
        entry = self.archived_entry()
        self.assertEqual(self.run_cli("flag", entry, "a token reached this entry").returncode, 0)
        failing = self.run_cli("check")
        self.assertEqual(failing.returncode, 1)
        self.assertIn(f"unresolved flag on entry {entry}", failing.stderr)
        resolved = self.run_cli("resolve", entry, "token rotated and entry redacted", "--redact")
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        archived = (self.archive / "feat-x.log").read_text()
        self.assertIn("redacted after flag", archived)
        self.assertNotIn("chose a split", archived)
        self.assertIn(f"id={entry}", archived)
        self.assertEqual(self.run_cli("check").returncode, 0)

    def test_redaction_hits_the_flagged_entry_even_after_another_archive_adds_older_entries(self):
        # regression: flags used to point at line numbers, and a later archive moved the line
        leaked = self.archived_entry("the leaked one", now="2026-11-09T12:00Z")
        self.run_cli("flag", leaked, "something reached this entry")
        self.add_id("decision", "a harmless older entry", wt="beta", now="2026-11-09T10:00Z")
        self.run_cli("archive", "--branch", "feat/x", "--wt", "beta")
        self.assertEqual(self.run_cli("resolve", leaked, "handled", "--redact").returncode, 0)
        archived = (self.archive / "feat-x.log").read_text()
        self.assertNotIn("the leaked one", archived)
        self.assertIn("a harmless older entry", archived)

    def test_a_rejected_resolve_note_changes_nothing(self):
        # regression: resolve --redact used to rewrite the archive before the note was checked
        entry = self.archived_entry()
        self.run_cli("flag", entry, "something reached this entry")
        before = (self.archive / "feat-x.log").read_text()
        result = self.run_cli("resolve", entry, "rotated token for host2", "--redact")
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.archive / "feat-x.log").read_text(), before)
        self.assertFalse((self.archive / "flags" / f"{entry}.resolved").exists())

    def test_ids_are_checked_before_any_path_is_built(self):
        # regression: a name like ../../../../outside/victim used to reach the filesystem
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "victim.flag").write_text("already here\n")
        traversal = "../../../../outside/victim"       # up from REPO/wiki/agent-log/flags to the temp root
        for command in (("flag", traversal, "x"), ("resolve", traversal, "x"), ("resolve", traversal, "x", "--redact")):
            self.assertEqual(self.run_cli(*command).returncode, 2, command)
        self.assertEqual(sorted(path.name for path in outside.iterdir()), ["victim.flag"])
        self.assertEqual(self.run_cli("flag", "0123456789", "no such entry").returncode, 2)

    def test_flag_and_resolve_state_is_checked(self):
        entry = self.archived_entry()
        self.assertEqual(self.run_cli("resolve", entry, "never flagged").returncode, 2)
        self.assertEqual(self.run_cli("flag", entry, "first flag").returncode, 0)
        self.assertEqual(self.run_cli("flag", entry, "second flag").returncode, 2)
        self.assertEqual(self.run_cli("resolve", entry, "done").returncode, 0)
        self.assertEqual(self.run_cli("resolve", entry, "again").returncode, 2)

    def test_flag_refuses_a_value_in_the_reason(self):
        entry = self.archived_entry()
        self.assertEqual(self.run_cli("flag", entry, "it was " + "gh" + "p_" + "a" * 12).returncode, 2)
        self.assertFalse((self.archive / "flags").exists())


if __name__ == "__main__":
    unittest.main()
