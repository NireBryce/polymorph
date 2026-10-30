# Lessons agents learned the hard way
_Last modified: 2026-10-30_

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
