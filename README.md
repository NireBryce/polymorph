# polymorph

> **Experimental and unvetted.** polymorph is pre-alpha. Its mappings have not been verified
> against live harnesses beyond the hook files they were written from, its adapter has run
> only in unit tests and one hand-fed run, and nobody outside the author has reviewed the
> code. It sits on a safety path (guard hooks), where a wrong mapping can fail open. Do not
> rely on it to enforce anything; treat `polymorph manage check` output as "the config is
> consistent", not "the guards work". Interfaces and the mapping schema may change without
> notice. Reports of problems are welcome.

## What polymorph does

Coding-agent harnesses (Claude Code, ZCode, Gemini CLI and others) each read their own
config files, name their tools and events differently, and disagree on what a hook's exit
code means. A repo that keeps guard hooks (a script that refuses `git reset --hard`, say)
for one harness has to hand-copy and hand-translate them for every other harness, and the
copies drift. A harness that does not recognise a file ignores it without saying so.

polymorph reads the repo's Claude Code agent config once and writes what each other
harness needs:

1. It **reads** `.agents/settings.json` and the hook scripts under `.agents/hooks/` into a
   canonical config.
2. It **generates** each enabled harness's own hook config from that canonical config and a
   per-harness **mapping** (a TOML file; see below).
3. It **reports gaps**: settings and files with no equivalent in a harness are listed, never
   silently dropped.
4. It **checks** statically that the generated files match what the mappings say.

What differs between harnesses (config file location, tool names, event names, what an exit
code means) lives in mappings and coverage tables, which are declarative TOML, not in code.
Python 3.11+, standard library only.

## Terms you will see

The full list is in [Vocabulary](docs/vocabulary.md). The ones you need first:

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

What works today, and what has been run:

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

Not done: skills, rules and `AGENTS.md`/`CLAUDE.md` are discovered and reported but not
transliterated into other harnesses' formats. The plan and its slices are in
[Loadout pivot plan](docs/plan-loadout-pivot.md).

## What it is not

- **Not a security boundary.** The hooks it wires up are usually string matchers over a
  command line. They catch honest mistakes, not a determined or confused agent.
- **Not proof that your guards work.** `polymorph manage check` is static. It never runs a
  hook, a harness or the network. A clean `check` means the generated files match what the
  mappings say and the scripts exist. It does not mean the harness calls the hook, honours
  a deny, or reads the file you wrote. See [Limits](docs/limits.md).
- **Not verified against live harnesses.** Every shipped mapping says in its `verified`
  field or its header comment what it actually rests on.
- **Not a way to run untrusted input.** Mappings are data; nothing in one is executed.
  Hook scripts must live under `.agents/hooks/`. See [Concepts](docs/concepts.md#trust-model).
- **Not tested on harnesses with built-in memory.** A loadout is a set of initial
  conditions loaded at every agent launch. On a harness that keeps memory across launches,
  reloading them can accumulate, duplicate or contradict what the memory already holds.
  This is untested and probably will stay so. See [Limits](docs/limits.md#built-in-memory).

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

Step 2 on a repo with a `permissions` block prints something like:

```
[zcode] would write .zcode/config.json (DRIFT)
  unmapped setting: permissions.allow (1 entries)
```

`unmapped setting` means polymorph found a setting with no cross-harness meaning and did
not translate it. `manage check` before step 3 fails with `generated-drift` because the
file does not exist yet; after step 3 it passes.

Commit `.zcode/config.json` and run `polymorph manage check --strict` in CI so the two
never disagree. Details: [Getting started](docs/getting-started.md), [CI](docs/ci.md).

## Commands

Every flag is in the [CLI reference](docs/cli.md).

**Maintaining a repo's agent config**

| Command | What it does |
|---|---|
| `manage generate` | Write each enabled harness's config from `.agents/settings.json`. `--check` writes nothing and exits 1 on drift. |
| `manage check` | Static analysis for CI: scripts exist and are executable, mappings valid, generated files current, fixtures replay. Text, JSON or SARIF. |
| `manage adopt` | Move agent files at rest into the basecamp store and symlink them back. |

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
| `validate` | Check one mapping file against schema 1. |
| `conformance` | Replay the shipped fixtures through the adapter. |
| `discover init` / `log` / `summary` / `draft` / `reload-check` | Record what an unmapped harness sends to hooks and draft a mapping from it. |
| `capture` | Run a real harness headless through fixed steps in a scratch directory and write a summary, a draft mapping and a report. Spends tokens: see [Cost and responsibility](#cost-and-responsibility). |

## Contributing

Start with [CONTRIBUTING](CONTRIBUTING.md). The most useful contributions right now are
reports of how a specific harness's hooks behave, and bug reports with a minimal
reproduction.

Where the code lives:

| Path | Role |
|---|---|
| `src/polymorph/cli.py` | argparse front end; one function per subcommand |
| `src/polymorph/reader_claude.py`, `canon.py` | read `.agents/settings.json` into the canonical config |
| `src/polymorph/mapping.py`, `validate.py` | load a mapping and validate it against schema 1 |
| `src/polymorph/generate.py`, `decisions.py` | canonical config + mapping -> harness config; recorded decisions for mismatches |
| `src/polymorph/adapter.py` | the runtime shim |
| `src/polymorph/check.py` | static analysis behind `manage check` |
| `src/polymorph/inventory.py`, `equip.py`, `adopt.py`, `env.py` | gap report, onboarding run, basecamp move, env contract |
| `src/polymorph/discover.py`, `capture.py` | record and draft from a live harness |
| `src/polymorph/mappings/`, `fixtures/`, `coverage/` | shipped per-harness data |
| `tests/` | unit tests (`fixture_repo/` is a sample agent config) |
| `docs/` | [SPEC](docs/SPEC.md) is the contract; the rest explains it |

Run the tests before sending a change (standard library only; please add no dependency):

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```

Project ground rules: mapping content is data and never executed; hook scripts resolve
under `.agents/hooks/`; a mapping without `verified` stays without it until a live session
backs it; a change that makes a doc stale fixes the doc in the same change. Never run
`polymorph capture` or a real harness without reading
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
