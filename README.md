# polymorph

> **Experimental and unvetted.** polymorph is pre-alpha. Only the `zcode` mapping carries
> `verified`, from two live probe sessions; `claude` rests on live payload logs without a
> deny seen to block, and `gemini` is an unchecked draft. Its adapter has run only in unit
> tests and one hand-fed run, and nobody outside the author has reviewed the code. It sits on a safety path (guard hooks), where a wrong mapping can fail open. Do not
> rely on it to enforce anything; treat `polymorph manage check` output as "the config is
> consistent", not "the guards work". Interfaces and the mapping schema may change without
> notice. Reports of problems are welcome.

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

polymorph is a loadout manager for coding-agent harnesses: it keeps a repo's **basecamp**
(a store of canonical agent files at `.polymorph/basecamp/`) and equips an arriving
harness with its **loadout** -- the generated hook configs, the gap report naming what
that harness will silently ignore, and the env contract for its sessions. What differs
between harnesses (config file location, tool names, event names, what an exit code
means) lives in small declarative TOML files called mappings and coverage tables, not in
code. Python 3.11+, standard library only.

The reason to want this: if you keep guard hooks (a script that refuses `git reset --hard`,
say) for one harness, you would otherwise hand-copy and hand-translate them for every other
one, and the copies drift. polymorph keeps one source of truth and regenerates the rest.

## Scope baseline — 2026-10-31

Written down before the scope moves, so later changes have something to be measured
against.

The goal at this point: walk a repository and read everything that interacts with an
agent's harness — `AGENTS.md`/`CLAUDE.md`, `.claude/`, and everything under `.agents/`
(settings, hooks, skills, rules, and the scripts those pieces call) — and transliterate
it into the files a given harness will actually use and honour, instead of letting each
harness silently ignore whatever it does not recognise.

What the tool does today, measured against that goal:

- **Reads** `.agents/settings.json` and the hook scripts under `.agents/hooks/` into a
  canonical config (three events, five tool classes). Settings with no cross-harness
  meaning are reported as unmapped, not dropped. Hook commands must be bare script paths
  that resolve under `.agents/hooks/`.
- **Transliterates** through declarative TOML mappings (`schema = 1`; `delivery` is
  `direct` or `adapter`). Shipped: `zcode` with `verified` text stating what its mapping
  rests on; `claude` and a `gemini` draft without it.
- **Generates** per-harness JSON hook configs from the `claude-settings` and
  `claude-like` templates, with `--check` for drift; repo-local mappings under
  `.polymorph/basecamp/mappings/` override the built-ins, with a warning.
- **Checks** statically, with stable finding codes and text, JSON, and SARIF 2.1.0
  output.

The implementation plan for the first move: [docs/plan-loadout-pivot.md](docs/plan-loadout-pivot.md). The words used for it across the docs, issues and PRs are fixed in [docs/vocabulary.md](docs/vocabulary.md).

The rest of the inventory in the goal — skills, rules, `AGENTS.md`/`CLAUDE.md`,
`.claude/` — was intent, not coverage when this was written.

**Delivered since this baseline** (see [the pivot plan](docs/plan-loadout-pivot.md)):
the discover-and-report inventory with per-harness coverage tables and the gap report
(`polymorph inventory`), `polymorph manage adopt` (store-and-symlink conversion), the
env contract (`polymorph env`), the onboarding run itself, `polymorph equip
--harness NAME`, and `polymorph discover probe`, which fills a mapping's `[capabilities]`
from a live session (`zcode`'s `verified` now rests on two such probe rounds).

## What it is not

- **Not a security boundary.** The hooks it wires up are usually string matchers over a
  command line. They catch honest mistakes, not a determined or confused agent.
- **Not proof that your guards work.** `polymorph manage check` is static. It never runs a hook,
  a harness or the network. A clean `check` means the generated files match what the
  mappings say and the scripts exist. It does not mean the harness calls the hook, honours
  a deny, or reads the file you wrote. See [Limits](docs/limits.md).
- **Not broadly verified against live harnesses.** Only `zcode` carries `verified`, and
  its header comment lists what is still a best guess. Every shipped mapping says in its
  `verified` field or its header comment what it actually rests on.
- **Not a way to run untrusted input.** Mappings are data; nothing in one is executed.
  Hook scripts must live under `.agents/hooks/`. See [Concepts](docs/concepts.md#trust-model).
- **Not tested on harnesses with built-in memory.** polymorph's output, and the loadouts
  described in the [loadout pivot plan](docs/plan-loadout-pivot.md) — sets of initial
  conditions meant to be loaded at every agent launch — are as-yet untested on agents
  whose harness keeps memory across launches, and probably never will be tested.
  Re-loading the same conditions at every launch can accumulate, duplicate or contradict
  what such a memory already holds, so the combination may cause adverse effects.
  See [Limits](docs/limits.md#built-in-memory).

## Install

```sh
nix run github:NireBryce/polymorph -- --help     # not run (the github ref); `nix run .#default` in a clone was
pip install -e .                                  # from a clone (plain `pip install .` works too)
python -m polymorph --help                        # after pip install -e ., or with PYTHONPATH=src
```

Either install works: the built-in mappings and fixtures ship inside the Python package
(`src/polymorph/mappings/`, `src/polymorph/fixtures/`), so a plain `pip install .` can run
`manage conformance` and `manage check` from any directory. The editable install is for working on polymorph.

## Five-minute quickstart

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
not translate it. It is reported, never silently dropped. `manage check` before step 3 fails with
`generated-drift` because the file does not exist yet; after step 3 it passes.

Commit `.zcode/config.json` and run `polymorph manage check --strict` in CI so the two never
disagree. Details: [Getting started](docs/getting-started.md), [CI](docs/ci.md).

## Commands

| Command | What it does |
|---|---|
| `manage generate` | Write each enabled harness's config from `.agents/settings.json`. `--check` writes nothing and exits 1 on drift. |
| `manage check` | Static analysis for CI: scripts exist and are executable, mappings valid, generated files current, fixtures replay. Text, JSON or SARIF. |
| `equip` | The onboarding run: writes the loadout for one harness and reports what was written, what the harness honours, what it will silently ignore, and the env contract. |
| `inventory` | The repo's discover-and-report surface and each harness's gap report. |
| `manage adopt` | Move agent files at rest into the basecamp store and symlink them back. |
| `env` | Emit the env contract from host facts, or run a command under it. |
| `adapter` | The runtime shim a generated config calls: translates a native hook payload, runs your script, translates the answer back. |
| `manage validate` | Check one mapping file against schema 1. |
| `manage conformance` | Replay the shipped fixtures through the adapter. |
| `discover init` / `log` / `summary` / `draft` / `reload-check` | Record what an unmapped harness sends to hooks and draft a mapping from it. |
| `discover probe` | Drive a scripted session through one marker command per decision route and print the `[capabilities]` block with how each flag was observed. Spends tokens; `zcode` is the only adapter. |
| `capture` | Run a real harness headless through fixed steps in a scratch directory and write a summary, a draft mapping and a report. Drivers: `opencode` runs; `claude` and `zcode` refuse to run and say why (neither has been seen working headless). Spends tokens: see Cost and responsibility. |

Every flag is in [CLI reference](docs/cli.md).

## Documentation

- [docs/index.md](docs/index.md): where everything is.
- [Getting started](docs/getting-started.md) and [Concepts](docs/concepts.md)
- [Writing a mapping](docs/writing-a-mapping.md) and [Discovery](docs/discovery.md)
- [CLI reference](docs/cli.md), [CI](docs/ci.md), [Limits](docs/limits.md)
- [SPEC](docs/SPEC.md): the contract the code is written against.
- [CONTRIBUTING](CONTRIBUTING.md) and [CHANGELOG](CHANGELOG.md)

## Tests

```sh
just test         # PYTHONPATH=src python3 -m unittest discover -s tests
just preflight    # what CI runs: tests, doc checks, lint, nix flake check, self-check
```

Tests use stub harnesses only; none of them runs a real model.

## License

Apache-2.0; see [LICENSE](LICENSE).
