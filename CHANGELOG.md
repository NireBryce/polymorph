# Changelog

polymorph is experimental. Anything here can change without notice; this file says what
changed and, from the first release that has users, how it breaks them.

## 0.0.1.dev0 (unreleased)

First state of the project. Python 3.11+, standard library only.

- **Directory:** the repo-side basecamp lives at `.polymorph/basecamp/` (`config.toml`,
  `mappings/` overrides, `decisions/`) — outside `.agents/`, so harnesses and inventory
  tools do not read a manager's storage as repo-provided agent config. The directory is
  named for the basecamp concept, not the tool: polymorph is one basecamp manager among
  potential others.
- **Reader:** reads `.agents/settings.json` and hook scripts under `.agents/hooks/` into a
  canonical config (three events, five tool classes). Settings with no cross-harness
  meaning are reported as unmapped, not dropped. Hook commands must be bare script paths
  that resolve under `.agents/hooks/`.
- **Mappings:** TOML, `schema = 1`, with `delivery = "direct"` or `"adapter"`. Shipped:
  `claude` and `zcode` (with `verified` text stating their basis) and a `gemini` draft with
  no `verified`.
- **`generate`:** writes JSON hook configs using the `claude-settings` and `claude-like`
  templates, with `--check` for drift. Lookup of mappings from the repo's
  `.polymorph/basecamp/mappings/` overrides the built-in ones with a warning.
- **`check`:** static analysis with stable finding codes; text, JSON and SARIF 2.1.0 output;
  `--strict`.
- **Decisions:** a hook that needs an event, tool class or payload field the target lacks is
  an undecided item until `.polymorph/basecamp/decisions/NAME.toml` answers it. `generate`
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
- **Review sweep, second pass:** `polymorph adapter` (the command `generate` writes into harness
  configs) now accepts `--timeout` and `--repo`, and hands its whole command line to the adapter,
  so a usage error is a visible NOT-checked message and not argparse's exit 2 (a deny on some
  harnesses; before, a hook with a `timeout` in the settings was written with a flag the CLI
  rejected). A deny the mapping cannot express (no `payload_out.deny`, `exit_2_blocks` and
  `json_answers` false) is NOT checked instead of exit 0. `apply_patch_paths` raises again on a
  `*** Begin Patch` envelope with no file header (it still returns nothing for text that only
  mentions `*** `). `check` reports malformed hook groups as `settings-invalid` instead of
  crashing. `equip` keeps other harnesses' entries in `loadout.toml` and reports an invalid
  `env.toml` instead of printing "no env.toml". Redacted discovery logs no longer keep raw
  `event`, `tool_name` or `session_id` text under the alias keys; a value that is not a plain
  name or id is logged as `<invalid>`. `discover init` no longer removes another harness's
  logger whose name ends with its own. `generate` refuses an existing config that is not a JSON
  object; the reader refuses a hook `timeout` that is not above 0; `env.toml` `exports` must be
  a list; `adopt` refuses a gitignored file.
- **Links:** `equip` and `manage adopt` create the symlinks that close `no-link` gaps
  (`CLAUDE.md -> AGENTS.md`, `.claude/skills -> ../.agents/skills`, `.claude/rules ->
  ../.agents/rules`), as relative links, only where nothing exists at the path the harness
  reads. A real file or directory and a symlink to somewhere else are left alone. Only
  built-in coverage tables create links. Each link is recorded in `loadout.toml` under
  `[links.NAME]`. `--no-links` on both commands skips this. The `fix` text of a `no-link` gap is
  now the command (`polymorph equip --harness claude links CLAUDE.md -> AGENTS.md`). Claude Code
  is the harness that needs them; ZCode reads `AGENTS.md` and `.agents/skills` itself.
- **Gap report (breaking, experimental):** the report now compares the path a harness reads
  with where the repo keeps the file. Each gap carries a `reason`: `unsupported`, `no-link`
  (Claude Code reads `CLAUDE.md`, `.claude/skills` and `.claude/rules`, and the repo has
  `AGENTS.md`, `.agents/skills`, `.agents/rules` with no link), `unlinked-copy` (both exist as
  separate real files or directories: drift) or `points-elsewhere`, and a `fix` text naming the
  link. `inventory --json` gains `reason`, `reads`, `fix`; `inventory.gaps()` takes the repo as
  a third argument. The built-in `claude` coverage table no longer lists `AGENTS.md` as read.
  polymorph does not make the links yet.
- **Discovery logs live in `.polymorph/discovery/`** (was `.polymorph-discovery/`). `--full` on
  `discover init` and `capture` now needs an explicit `--log-dir` / `--out` (else a loud
  refusal), the long `--i-really-want-this-in-the-repo-raw` flag (also spelled
  `--i-really-want-this-in-the-repo`), and then a typed `yes`; with no terminal it is refused.
  The log stays plain text: no encryption, so secret scanners can still see a leak.
- **Command reference:** `docs/usage.md` and `man/polymorph.1` are written from the argparse
  parser by `scripts/gen_reference.py` (`just reference`); `just docs` and the unit tests fail
  when they are stale. Every flag now has help text. Skill `promote-to-main` covers bringing
  `experimental` to `main`. The man page text is checked against the parser; it has not been
  rendered by `man` here.
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
- **`inventory` and coverage tables:** the discover-and-report kinds (skills, rules,
  memory, `.claude/`) are read as paths and symlink targets only, never contents; every
  found path is reported. Per-harness coverage tables (`src/polymorph/coverage/*.toml`,
  repo-local overrides at `.polymorph/basecamp/coverage/`, same lookup and trust rules as
  mappings) say what each harness honours; `polymorph inventory` prints the surface plus
  each harness's gap report — provided paths the harness will silently ignore. A harness
  with no coverage table is unknown, never a gap. Shipped for `claude` and `zcode`.
- **`adopt`:** moves a repo's agent files at rest (`AGENTS.md`, `CLAUDE.md`,
  `.agents/skills/`, `.claude/rules/`, `.agents/rules/`) into the basecamp store and
  replaces each original path with a relative symlink. Refuses a path that is already a
  symlink, uncommitted changes (git status; a non-git directory refuses everything) and a
  store copy that already exists beside the at-rest file; a second run is a no-op; hook
  scripts and `.agents/settings.json` are never moved.
- **`env`, the env contract:** host facts in `.polymorph/basecamp/env.toml` (the machine
  key's rendered path and scope, the vault-socket names a session must not inherit,
  per-harness session markers) become shell exports — `polymorph env [--harness NAME]`
  emits them, `--exec CMD` runs CMD under them and exits with its status. Emit-only: the
  key is referenced by path, its contents are never read; nothing is written; no daemon.
  Harness names come from the host facts and the mappings, never a hardcoded list.
- **Docs:** `docs/observed-zcode-session.md` — what the author's own live ZCode 3.16.2
  session showed (session markers, no inherited agent socket, AGENTS.md ingestion,
  skills loading through the basecamp-store symlink), claims kept apart by how they
  were observed; the zcode coverage table's basis note cites it.
- **`equip`, the onboarding run (slice 3):** `polymorph equip --harness NAME` composes
  the loadout -- `manage generate`'s config files, the inventory's honoured surface and
  gap report, and the env contract -- into one summary, and records a provenance
  manifest (`.polymorph/basecamp/loadout.toml`: per issued artifact, its sha256 and
  source). Posture-aware: a committed basecamp gets committed artifacts; in a
  contributor checkout the artifacts are regenerable and `--gitignore` appends a marked,
  idempotent `.gitignore` section. `--dry-run` writes nothing. The maintainer half is
  now a command group -- `polymorph manage generate|check|validate|conformance|adopt`
  (the old top-level names are gone); `equip`, `inventory`, `env`, `adapter`,
  `discover` and `capture` stay top-level.
