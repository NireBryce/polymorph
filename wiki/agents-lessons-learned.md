# Lessons agents learned the hard way
_Last modified: 2026-10-31_

Past mistakes from building this project, one line each. **Home** is where
the rule is enforced or stated; a lesson with no Home reaches nobody.
Skill `record-a-lesson` adds rows.

| Lesson | Rule | Home |
|---|---|---|
| A test helper's default argument silently kept a test exercising the old behaviour after the code changed. | When a change adds a parameter, grep the test helpers for the default and pass the new case explicitly; break the code and watch the test fail. | `AGENTS.md` (check shown to fail), skill `review` |
| `cmd \| tail` exited 0 while `cmd` failed. | A pipe's status is the last command's: do not pipe a check, or use `set -o pipefail`. Read the output, not the status. | `AGENTS.md` Commands |
| A new test file was invisible to `nix flake check`. | Flakes ignore untracked files: `git add` before checking. | `AGENTS.md` Commands, skill `use-a-worktree` |
| `nix flake check --no-build` passed on a check that does its work at build time. | `--no-build` only evaluates the derivation; run the real check (`just check`) without it before claiming the suite passes. | none yet |
| A hand-written `.zcode/config.json` was rewritten by the tool into its own normal form, so `generate --check` reported drift forever. | Generate a tool-managed config in the tool's own normalized form, and test that it round-trips. | skill `new-mapping` (live verification) |
| A denylist of secret-looking keys in discovery logs missed new ones. | Redact by allowlist: keep only named structural values, replace everything else with `{type, length}`. | `docs/SPEC.md` "Privacy", skill `new-mapping` |
| A logger that read the environment leaked variable values into a log. | Reading the environment is opt-in and loud (`--env-be-very-careful`), and logs names only. | `docs/SPEC.md` "Privacy" |
| `src/polymorph.egg-info/` appeared in the working tree after an editable install. | Build output is gitignored (`*.egg-info/`); `just lint` fails if any is tracked. | `.gitignore`, `tests/lint.py` |
| A check that had never failed was trusted. | Every checker ships with a test on a bad input it must flag (`tests/test_docs.py`), and you see it fail before relying on it. | `AGENTS.md`, skill `review` |
| A test that read the ambient environment passed locally and failed on a CI runner: the environment-name test saw `GITHUB_WORKSPACE`. | A test about environment names (or anything the environment can change) runs the CLI with `PATH` only (`run_cli(..., clean=True)` in `tests/test_discover.py`) and adds the variables it needs itself. Run such a test once with extra variables set to see it hold. | `AGENTS.md` Hard rules, skill `new-mapping` (step 5), `tests/test_discover.py` |
| A lint that matched substrings missed what it should catch: `build/` was tracked because the check listed `.egg-info/` only. | Match directory components, not substrings (so `docs/build-notes.md` stays fine), and test each kind of bad input separately, one subtest per kind. | `tests/lint.py`, `tests/test_lint.py` |
| A reported cause was taken as the finding and turned out wrong (three times in one session). | A reported cause is a hypothesis until the code agrees: find the line that produces the symptom and make a test fail for that reason before fixing anything. | skill `investigate-bug` (steps 3-4) |
| A step that goes outward (merge, push, delete) ran before the exit status of the check before it was read. | Read the exit status, not the output you expected, before the first step that goes outward. `just ci-wait` and `just ship-land` exist so that the check and the merge are one refusal, not two things to remember. | skill `ship` (steps 2-3), `AGENTS.md` Hard rules |
| `ship_land.py` merged a PR and then deleted its head branch by name after checking only the base; a fork PR whose head was named `main` would have aimed the delete at the real branch. | A step that deletes by name takes the name from facts read off the PR (`isCrossRepository`, head name and SHA), refuses fork, protected and base-equal heads before merging, and uses `-d` unless the local tip is the verified head. A missing field counts as unsafe. | skill `ship`, `scripts/ship_land.py` and `tests/test_workflow_scripts.py` |
| `capture` killed the harness's process group the moment the harness exited, which also killed the logger children that write late; every test passed because its stub harness logged synchronously. | A stub of something asynchronous must be asynchronous too (a child that writes after the parent exits), or the test cannot see ordering bugs. Settle the log before killing; kill at once only on timeout or signal. | `tests/test_capture.py`, `src/polymorph/capture.py` (`run_step`) |
