# Changelog

polymorph is experimental. Anything here can change without notice; this file says what
changed and, from the first release that has users, how it breaks them.

## 0.0.1.dev0 (unreleased)

First state of the project. Python 3.11+, standard library only.

- **Reader:** reads `.agents/settings.json` and hook scripts under `.agents/hooks/` into a
  canonical config (three events, five tool classes). Settings with no cross-harness
  meaning are reported as unmapped, not dropped. Hook commands must be bare script paths
  that resolve under `.agents/hooks/`.
- **Mappings:** TOML, `schema = 1`, with `delivery = "direct"` or `"adapter"`. Shipped:
  `claude` and `zcode` (with `verified` text stating their basis) and a `gemini` draft with
  no `verified`.
- **`generate`:** writes JSON hook configs using the `claude-settings` and `claude-like`
  templates, with `--check` for drift. Lookup of mappings from the repo's
  `.agents/polymorph/mappings/` overrides the built-in ones with a warning.
- **`check`:** static analysis with stable finding codes; text, JSON and SARIF 2.1.0 output;
  `--strict`.
- **Decisions:** a hook that needs an event, tool class or payload field the target lacks is
  an undecided item until `.agents/polymorph/decisions/NAME.toml` answers it. `generate`
  writes nothing for that target and exits 3 (it used to skip and report, exit 0); `check`
  gains `decision-undecided`, `decision-weakens-guard`, `decision-stale`, `decision-invalid`
  and drops `hook-no-equivalent`.
- **`adapter`:** runtime shim translating native payloads to the canonical protocol and
  answers back, exporting `GUARD_*` capability variables; faults produce a visible
  "NOT checked" message and never a deny.
- **`validate` and `conformance`:** mapping validation and fixture replay, with fixtures for
  `claude` and `zcode`.
- **`discover`:** `init`, `log`, `summary`, `draft`, `reload-check`. Logs are redacted by
  default; `--full` and `--env-be-very-careful` are opt-ins. Drafts are unverified and
  built only from observed names.
- **`capture`:** `polymorph capture --harness NAME` runs the discovery procedure itself:
  scratch directory, logger installed by a shipped driver (`opencode`; `claude`, blocked until confirmed live),
  fixed steps through the harness's headless mode, then `summary.txt`, `draft.toml`,
  `report.md` and `log.jsonl`, and cleanup. It spends tokens, so nothing runs without a typed
  `yes` after a cost warning (`--yes` still prints a notice; a non-interactive stdin without it
  refuses). The harness runs with `PATH`, `HOME` and `XDG_*` only. Acceptance for issue 16, as
  met so far: the stub-harness tests cover confirmation, cancel, cleanup, timeouts, Ctrl-C,
  scratch-only writes and the environment; it has not been run against a real harness.
  `--print-prompts` prints the manual prompt kit. README gains "Cost and responsibility".
- **`capture` and signals:** SIGTERM and SIGHUP now run the same cleanup as Ctrl-C (kill the
  harness's process group, remove the scratch directory) and exit 143 and 129; the previous
  handlers are restored afterwards. A signal that arrives during that cleanup no longer cuts
  it short: the cleanup runs again to the end, and the exit status is the signal's.
- **`capture` keeps late log lines:** when the harness exits by itself, its process group is no
  longer killed at once (that killed the plugin's logger children before they wrote); the
  step waits, up to 10s, until the group is empty and the log has stopped growing. A timeout
  or a signal still kills it immediately.
- **`capture` scratch directory:** made with `mkdtemp` directly in the system temp dir
  (`polymorph-capture-NAME-XXXXXXXX`), no shared `/tmp/polymorph-capture` parent. Cancelling
  with `--keep` no longer prints both "no scratch directory was left" and "kept scratch directory".
- **`capture` argument checks:** `--out` that cannot be created or written, and a
  `--step-timeout` or `--total-timeout` that is not finite and above 0, exit 2 before the
  question and before any step runs.
- **OpenCode capture plugin:** tool-hook registration is wrapped in try/catch so a synchronous
  throw cannot escape `setup()`; the logger is spawned as `sys.executable` directly (no
  `/usr/bin/env`), with `PYTHONPATH` as its entire environment via the spawn `env` option. The
  example in `docs/examples/` gains the matching `POLYMORPH_ENV` constant. The JavaScript was
  not run (no node in the tests); the tests read the rendered text.
- **`ship-land` refusals:** before merging it now refuses a PR whose head is in another
  repository, whose head branch equals the base or is `main`, `master`, `experimental` or the
  repo's default branch, or whose base is not the default branch (`--allow-base BRANCH` for a
  stacked PR). The local branch is deleted with `-d`, or `-D` only when it is exactly the
  verified head; the remote branch is kept while another open PR is based on it.
- **`discover` event names:** names may contain `.`, `-` and `:` (OpenCode sends
  `execute.before`, `execute.after`, `session.created`); the draft guesses `pre_tool` and
  `post_tool` from a trailing `before` / `after`, marked `guessed from name`, lists other
  observed names in a comment under `[events]`, and `summary` shows the real names. Mapping
  validation accepts the same characters in `[events]` values.
- **`discover summary` and `draft` on noisy captures:** tool events only by default
  (`--include EVENT` adds, `--event EVENT` selects); lines sorted by `ts`; `before` and
  `after` paired by call id and message id with unpaired ones reported; output-like objects
  keep nested key paths (depth-limited, values still `{type, length}`) and the draft lists
  them under `[payload_out]`; boolean `tool_input` keys such as `background` are listed;
  log lines carry `schema: 1` (older lines still read). Call and message ids that look like
  ids are now logged verbatim so pairing works.
- **Docs:** `docs/observed-opencode.md` (what a live OpenCode v2.0.32 capture showed, with
  documented, binary-only and untested claims kept apart) and an example discovery plugin.
- **Report path:** a `report-harness` agent skill and a Harness report issue form.
- **Docs:** getting started, concepts, writing a mapping, CLI reference, discovery, CI and
  limits.
- Packaging: a Nix flake and `pyproject.toml`. Built-in mappings and fixtures are package
  data under `src/polymorph/`, so a plain `pip install .` works.
