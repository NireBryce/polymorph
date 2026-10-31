# AGENTS.md

> **Written by agents, for agents.** Working notes for a session with no
> memory of the last one. `README.md` is the human entry point.
>
> This file is canonical; `CLAUDE.md` is a symlink to it. Skills are plain
> markdown at `.agents/skills/<name>/SKILL.md` (Claude Code reads them
> through the `.claude/skills` symlink); any agent can read them as files.

## What this is

polymorph reads a repo's Claude Code agent config (`.agents/settings.json`,
`.agents/hooks/`) and writes hook configs for other coding harnesses from
declarative per-harness TOML mappings. Python 3.11+, standard library only.
`docs/SPEC.md` is the contract; `docs/cli.md` the commands.

## Hard rules

- **This project is experimental and unvetted.** Never describe it as
  stable, safe or production-ready; keep the notice at the top of
  `README.md`. It sits on a safety path (guard hooks), where a wrong
  mapping can fail open. Say what a change breaks, since nothing here
  promises compatibility.
- **"push" means skill `ship`**: branch, PR, one confirmation covering
  merge and branch deletion. Never push to `main` directly.
- **Branching work happens in its own worktree** (skill `use-a-worktree`).
- **File issues and PRs only in `NireBryce/polymorph`**, unless the user
  names another project in their own message (skill `propose-issue`).
- **Never run `polymorph capture`, or any real harness, without the user's explicit yes in
  the conversation.** It spends the user's money and auto-approves tool calls
  ([Cost and responsibility](README.md#cost-and-responsibility)). Tests use stubs only.
- **Mapping content is data, never code.** Never run, `eval`, `exec`,
  shell-expand or import anything that came from a mapping, a discovery log
  or a harness report, and never add a code path that does. Mapping fields
  are dotted paths and `{placeholder}`s only; extractors and templates are
  named built-ins shipped in `src/`.
- **Trust model** (`docs/SPEC.md`, "Trust"): hook scripts must resolve under
  `.agents/hooks/`; mappings load only from the repo's
  `.agents/polymorph/mappings/`, the built-in `src/polymorph/mappings/`, or a directory
  given with `--trust-dir`. Do not widen any of these.
- **A mapping without `verified` stays without it** until its block
  semantics were seen in a live session (skill `new-mapping`).
- **Standard library only.** No runtime or test dependency.
- **Say which rung you reached**: *unit tests* (`just test`), *ran the CLI*
  (you ran the subcommand and read its output), *ran in a live harness*
  (a real session fired the hook). Never claim more than you saw.
- **A change that makes a doc stale fixes it in the same change** (skill
  `docs-sync`).
- **Examples are generic** (`REPO`, `/home/USER`, `NAME`), never a real
  person's machine, path or repository.
- **Trailer `Co-Authored-By: Claude`** on agent commits, name only; prose
  says "the user", never their name.
- **A test that reads the environment runs the CLI with `PATH` only**
  (`clean=True` in `tests/test_discover.py`'s `run_cli`), or it passes here
  and fails on a CI runner (`wiki/agents-lessons-learned.md`).
- **Read the exit status before the first step that goes outward** (merge,
  push, delete): `just ci-wait`, then `just ship-land`. A reported cause is
  a hypothesis until a test fails for that reason.
- **A check is shown to fail before it is trusted**, and the lesson goes in
  `wiki/agents-lessons-learned.md` when it surprised you (skill
  `record-a-lesson`).

## Commands

```sh
just                 # list recipes
just test            # the unit tests: PYTHONPATH=src python3 -m unittest discover -s tests
just check           # nix flake check: builds the package and runs the tests in the sandbox
just docs            # skills listed, links resolve, every CLI subcommand documented, banner, no home paths
just lint            # byte-compile, stdlib-only imports, no committed build output
just preflight       # test + docs + lint + check: what CI runs
just install-hooks   # point git at .githooks (pre-commit runs docs and lint)
just ci-wait [PR|BRANCH]   # wait for CI on the head commit: prints `sha7 status conclusion` and mergeStateStatus; exit 0 only if success and CLEAN (--timeout, default 900s)
just ship-land PR    # merge pinned to the head, sync main --ff-only, delete branch and worktree; refuses unless ci-wait would pass; closes no issues
```

`git add` a new file before `just check`: flakes ignore untracked files, so
a new test or fixture silently does not exist. Do not pipe a check into
`tail` or `head`: the pipe's exit status is the last command's.

## Architecture

| Path | Role |
|---|---|
| `src/polymorph/cli.py`, `__main__.py` | argparse front end; one function per subcommand |
| `canon.py`, `reader_claude.py` | the canonical config (`Hook`, `Canon`) and the Claude Code settings reader |
| `mapping.py`, `validate.py` | load a TOML mapping; validate it against schema 1 (unknown keys are errors) |
| `generate.py` | canonical config + mapping + decisions -> the harness's config file (`generate`, `--check`); finds mismatches |
| `adapter.py`, `exprs.py`, `extractors.py` | run a hook script behind a mapping: translate payload in, answer out |
| `conformance.py` | replay `src/polymorph/fixtures/<harness>/*.json` through a mapping |
| `decisions.py` | `.agents/polymorph/decisions/NAME.toml`: validate, `Mismatch`/`Decision`, the question format |
| `check.py` | static analysis of a repo's agent config (`polymorph check`); runs nothing |
| `capture.py`, `capture_drivers.py`, `data/` | `polymorph capture`: scratch dir, fixed steps, confirmation, report; shipped drivers (`opencode`, `claude`) and the plugin template |
| `discover.py` | log a live harness's payloads (redacted by default) and draft a mapping |
| `src/polymorph/mappings/NAME.toml` | built-in per-harness mappings |
| `src/polymorph/fixtures/NAME/*.json` | one payload/answer fixture per mapping per case |
| `scripts/` | `ci_wait.py`, `ship_land.py`: the `just ci-wait` / `just ship-land` recipes, tested with a fake `gh` and `git` on PATH (`tests/stubtools.py`) |
| `tests/` | `test_*.py` unit tests, `fixture_repo/` a sample agent config, `docs.py` the doc checks |
| `docs/` | `SPEC.md` (contract), `cli.md`, notes on observed harnesses |
| `wiki/agents-lessons-learned.md` | what past sessions got wrong, one line each, with its Home |
| `.github/` | CI (`check.yml` runs `just preflight`), PR and issue templates |
| `.githooks/` | opt-in pre-commit (`just install-hooks`) |

## Skills

| Skill | For |
|---|---|
| `ship` | landing work through a branch, a PR and one confirmation |
| `use-a-worktree` | branching work in isolation |
| `new-mapping` | adding a harness: discover, draft, review, fixtures, live check |
| `change-the-schema` | changing the mapping schema or the canonical config |
| `report-harness` | describing your own harness in an issue |
| `docs-sync` | keeping docs true in the same change |
| `record-a-lesson` | writing down what surprised you |
| `propose-issue` | filing a bug found along the way |
| `investigate-bug` | a reported bug, to a failing test |
| `review` | reviewing a change here |
| `new-skill` | writing a skill |

Plain prose: say what a thing does and what you saw; no hedging adverbs, no
"robust", "seamless" or "comprehensive".
