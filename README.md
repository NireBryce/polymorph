# polymorph

> **Experimental and unvetted.** polymorph is pre-alpha. Only the `zcode` mapping has been
> checked in a live session, and that check found ZCode ignores JSON hook answers. The
> `claude` mapping rests on hook files and captured payloads; `gemini` is a draft written
> from an issue. The adapter has run only in unit tests and one hand-fed run, and nobody
> outside the author has reviewed the code. It sits on a safety path (guard hooks), where a
> wrong mapping can fail open. Do not rely on it to enforce anything; treat
> `polymorph manage check` output as "the config is consistent", not "the guards work".
> Interfaces and the mapping schema may change without notice. Reports of problems are
> welcome.

## What polymorph does

polymorph converts a repo's Claude Code hook configuration into the hook configuration
files of other coding-agent harnesses, such as ZCode and Gemini CLI.

Harnesses read different config files, name tools and events differently, and disagree on
what a hook's exit code means. A repo that keeps guard hooks for one harness (for example,
a script that refuses `git reset --hard`) has to copy and translate them by hand for every
other harness, and the copies drift apart. A harness that does not recognise a file
ignores it without a warning.

polymorph works in four steps:

1. It **reads** `.agents/settings.json` and the hook scripts under `.agents/hooks/` into a
   canonical config.
2. It **generates** each enabled harness's hook config from that canonical config and a
   per-harness **mapping** (a TOML file; see below).
3. It **reports gaps**: it lists the settings and files that have no equivalent in a
   harness.
4. It **checks** statically that the generated files match what the mappings say.

The differences between harnesses (config file location, tool names, event names, exit
code meanings) are recorded in mappings and coverage tables, which are declarative TOML
files. polymorph needs Python 3.11+ and uses only the standard library.

## Terms

The full list is in [Vocabulary](docs/vocabulary.md).

| Term | Meaning |
|---|---|
| **canonical config** | The harness-neutral form of the repo's hooks and settings, read from the Claude Code layout. |
| **mapping** | A TOML file (`schema = 1`) describing one harness: config path, tool and event names, capabilities, payload layout, answer format. Data only; nothing in it is executed. |
| **coverage table** | A per-harness TOML file listing which kinds of repo files (skills, rules and so on) the harness reads. A kind the repo has and the table lacks is a gap. |
| **basecamp** | The repo's store of canonical agent files at `.polymorph/basecamp/`. The paths agents expect (`AGENTS.md`, `.agents/skills/`) are symlinks into it. |
| **loadout** | What polymorph hands a harness: generated hook configs, pointers to basecamp files, the env contract and the gap report. |
| **equip** | The verb: `polymorph equip --harness NAME` writes the loadout for one harness. |
| **gap report** | The part of the loadout naming what the harness will silently ignore even after equipping. |
| **env contract** | Shell exports from `polymorph env` (agent key path, sockets a session must not inherit, harness session markers). |
| **adapter** | An optional runtime shim: a generated config calls it, it translates the harness's native hook payload to the canonical one, runs your script, and translates the answer back. |

How these fit together: [Concepts](docs/concepts.md).

## Status

Implemented and covered by unit tests:

- **Reads** `.agents/settings.json` and `.agents/hooks/` into a canonical config (three
  events, five tool classes). Settings with no cross-harness meaning are reported as
  unmapped. Hook commands must be bare script paths that resolve under `.agents/hooks/`.
- **Generates** JSON hook configs from the `claude-settings` and `claude-like` templates,
  with `--check` for drift. Repo-local mappings under `.polymorph/basecamp/mappings/`
  override the built-ins, with a warning.
- **Ships mappings** for `zcode` (with a `verified` field stating what it rests on),
  `claude` and a `gemini` draft (neither has `verified`).
- **Checks** statically, with stable finding codes and text, JSON and SARIF 2.1.0 output.
- **Inventories and equips**: `polymorph inventory` (coverage tables and the gap report),
  `polymorph manage adopt` (move files into the basecamp and symlink them back),
  `polymorph env` and `polymorph equip --harness NAME`.

Only settings and hooks are translated. Skills, rules and memory files
(`AGENTS.md`/`CLAUDE.md`) are discovered and reported, not rewritten: harnesses read them as
plain files, so `equip` creates a symlink where a harness expects a path that does not
exist. The plan is in [Loadout pivot plan](docs/plan-loadout-pivot.md).

Run in a live harness:

- **ZCode**: two probe rounds on 2026-11-04. Exit 2 blocks a call; JSON answers (deny,
  ask) are ignored, so a guard that answers JSON deny fails open there; the post-tool event
  fires; input rewrites are not honoured. Details:
  [Observed: a live ZCode session](docs/observed-zcode-session.md).
- **Claude Code**: payloads from a live session matched the mapping, but no deny has been
  seen to stop a call, so the `claude` mapping has no `verified` field.
- **Gemini CLI**: nothing; the `gemini` mapping is a draft.

The per-mapping basis is in [Limits](docs/limits.md#what-verified-means).

## What it is not

- **Not a security boundary.** The hooks it wires up are usually string matchers over a
  command line. They catch common mistakes. They do not stop an agent that works around
  them.
- **Not proof that your guards work.** `polymorph manage check` is static. It never runs a
  hook, a harness or the network. A clean `check` means the generated files match what the
  mappings say and the scripts exist. It does not mean the harness calls the hook, honours
  a deny, or reads the file you wrote. See [Limits](docs/limits.md).
- **Not verified for real use.** Live checks so far are listed under [Status](#status).
  Every shipped mapping says in its `verified` field or its header comment what it rests
  on.
- **Not a way to run untrusted input.** Mappings are data; nothing in one is executed.
  Hook scripts must live under `.agents/hooks/`. See [Concepts](docs/concepts.md#trust-model).
- **Not tested on harnesses with built-in memory.** A loadout is a set of initial
  conditions loaded at every agent launch. On a harness that keeps memory across launches,
  reloading them can accumulate, duplicate or contradict what the memory already holds.
  No such test is planned. See [Limits](docs/limits.md#built-in-memory).

## Cost and responsibility

`polymorph capture`, and any manual procedure that drives an AI coding harness (including
the prompt kit in [Discovery](docs/discovery.md)), runs a real AI model on your account.

- That spends tokens. If the harness is billed per token on an API key, it costs real
  money. On a subscription plan it counts against the plan's usage limits and may be
  billed as extra usage.
- polymorph cannot tell which of these applies to you, and it cannot measure or cap the
  cost. Each step may take several model turns, and the tool shows the number of steps, not
  a price.
- Tool calls are auto-approved inside a scratch directory the tool creates. The prompts
  are fixed, but a model can misbehave regardless, and the scratch directory is a working
  directory, not a sandbox.
- You decide whether to run it. You are responsible for the cost and for checking your
  provider's terms for use through third-party tools. Nothing runs unless you type `yes`
  (or pass `--yes`, which you should do only knowing all of the above).
- polymorph is experimental and unvetted and, as the Apache-2.0 licence says, is provided
  without warranty. The maintainers accept no liability for charges, data loss or action
  taken against an account.

## Install

```sh
pip install -e .                       # from a clone; plain `pip install .` also works
python -m polymorph --help             # or: PYTHONPATH=src python3 -m polymorph --help
nix run github:NireBryce/polymorph -- --help   # not run; `nix run .#default` in a clone was
```

The built-in mappings and fixtures ship inside the Python package
(`src/polymorph/mappings/`, `src/polymorph/fixtures/`), so a plain install can run
`conformance` and `check` from any directory. The editable install is for working on
polymorph.

## Quickstart

You have a repo with `.agents/settings.json` and hook scripts under `.agents/hooks/`, and
you want ZCode to run the same hooks.

```sh
# 1. Say which harnesses to generate for.
mkdir -p .polymorph/basecamp
printf 'enabled = ["zcode"]\n' > .polymorph/basecamp/config.toml

# 2. See what would be written, and what has no equivalent. Writes nothing.
python -m polymorph manage generate --check

# 3. Write .zcode/config.json.
python -m polymorph manage generate

# 4. Check the repo is consistent. Exit 0 means no errors.
python -m polymorph manage check
```

Commit `.zcode/config.json` and run `polymorph manage check --strict` in CI, so CI fails
when the committed file no longer matches `.agents/settings.json`.
[Getting started](docs/getting-started.md) walks through these steps with their output;
[CI](docs/ci.md) covers pre-commit and GitHub Actions.

## Commands

Every flag is in the [CLI reference](docs/cli.md).

**Maintaining a repo's agent config**

| Command | What it does |
|---|---|
| `manage generate` | Write each enabled harness's config from `.agents/settings.json`. `--check` writes nothing and exits 1 on drift. |
| `manage check` | Static analysis for CI: scripts exist and are executable, mappings valid, generated files current, fixtures replay. Text, JSON or SARIF. |
| `manage adopt` | Move agent files at rest into the basecamp store and symlink them back. |
| `manage validate` | Check one mapping file against schema 1. |
| `manage conformance` | Replay the shipped fixtures through the adapter. |

**Equipping a harness that arrives in the repo**

| Command | What it does |
|---|---|
| `equip` | The onboarding run: writes the loadout for one harness and reports what was written, what the harness honours, what it will silently ignore, and the env contract. |
| `inventory` | The repo's discover-and-report surface and each harness's gap report. |
| `env` | Emit the env contract from host facts, or run a command under it. |

**At hook run time**

| Command | What it does |
|---|---|
| `adapter` | The runtime shim a generated config calls: translates a native hook payload, runs your script, translates the answer back. |

**Adding support for a harness**

| Command | What it does |
|---|---|
| `discover init` / `log` / `summary` / `draft` / `reload-check` | Record what an unmapped harness sends to hooks and draft a mapping from it. |
| `discover probe` | Drive a scripted session through one marker command per decision route and print the `[capabilities]` block with how each flag was observed. `zcode` is the only adapter. Spends tokens: see [Cost and responsibility](#cost-and-responsibility). |
| `capture` | Run a real harness headless through fixed steps in a scratch directory and write a summary, a draft mapping and a report. Drivers: `opencode` runs; `claude` and `zcode` refuse to run and say why (neither has been seen working headless). Spends tokens: see [Cost and responsibility](#cost-and-responsibility). |

## Contributing

[CONTRIBUTING](CONTRIBUTING.md) covers reporting a harness, reporting a bug, adding a
mapping and the rules for code and doc changes. The most useful contributions right now
are reports of how a specific harness's hooks behave, and bug reports with a minimal
reproduction. This section is the map you need before opening the code.

Where the code lives:

| Path | Role |
|---|---|
| `src/polymorph/cli.py` | argparse front end; one function per subcommand |
| `src/polymorph/reader_claude.py`, `canon.py` | read `.agents/settings.json` into the canonical config (`Hook`, `Canon`) |
| `src/polymorph/canonical.py` | the canonical payload keys and where each sits in a Claude payload |
| `src/polymorph/mapping.py`, `validate.py` | load a mapping and validate it against schema 1 |
| `src/polymorph/generate.py`, `decisions.py` | canonical config + mapping -> harness config; recorded decisions for mismatches |
| `src/polymorph/adapter.py`, `exprs.py`, `extractors.py` | the runtime shim, its dotted-path expressions and its named built-in extractors |
| `src/polymorph/conformance.py` | replay fixtures through a mapping |
| `src/polymorph/check.py` | static analysis behind `manage check` |
| `src/polymorph/inventory.py`, `equip.py`, `links.py` | gap report, onboarding run, the symlinks `equip` makes for `no-link` gaps |
| `src/polymorph/adopt.py`, `env.py` | the move into the basecamp store; the env contract |
| `src/polymorph/discover.py` | log a live harness's payloads and draft a mapping |
| `src/polymorph/capture.py`, `capture_drivers.py` | `capture`: the fixed steps and the per-harness drivers |
| `src/polymorph/probe.py` | `discover probe`: observe capability flags in a live run |
| `src/polymorph/resources.py` | locate the shipped data directories |
| `src/polymorph/mappings/`, `fixtures/`, `coverage/`, `data/` | shipped per-harness data, and templates used by `capture` and `probe` |
| `tests/` | unit tests (`fixture_repo/` is a sample agent config) |
| `scripts/` | the `ci-wait`, `ship-land` and reference-generation recipes; `run_recipe.py` runs any recipe without `just` |
| `docs/` | [SPEC](docs/SPEC.md) is the contract; the rest explains it |

Run the checks before sending a change. Do not add dependencies.

```sh
just test            # unit tests: PYTHONPATH=src python3 -m unittest discover -s tests
just preflight       # what CI runs: test, docs, lint, nix flake check, self-check
just install-hooks   # optional: a pre-commit hook that runs docs and lint
```

`just` lists every recipe. Without `just` installed, `python3 scripts/run_recipe.py
RECIPE` runs a recipe from the justfile. `check`, and so `preflight`, need Nix. The tests
use stub harnesses only; none of them runs a real model.

Before running `polymorph capture`, `discover probe` or a real harness, read
[Cost and responsibility](#cost-and-responsibility).

## Documentation

- [docs/index.md](docs/index.md): where everything is.
- [Getting started](docs/getting-started.md) and [Concepts](docs/concepts.md)
- [Writing a mapping](docs/writing-a-mapping.md) and [Discovery](docs/discovery.md)
- [CLI reference](docs/cli.md), [CI](docs/ci.md), [Limits](docs/limits.md)
- [SPEC](docs/SPEC.md): the contract the code is written against.
- [Vocabulary](docs/vocabulary.md) and [Loadout pivot plan](docs/plan-loadout-pivot.md)
- [CONTRIBUTING](CONTRIBUTING.md) and [CHANGELOG](CHANGELOG.md)

## License

Apache-2.0; see [LICENSE](LICENSE).
