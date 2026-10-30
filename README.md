# polymorph

> **Experimental and unvetted.** polymorph is pre-alpha. Its mappings have not been verified
> against live harnesses beyond the hook files they were written from, its adapter has run
> only in unit tests and one hand-fed run, and nobody outside the author has reviewed the
> code. It sits on a safety path (guard hooks), where a wrong mapping can fail open. Do not
> rely on it to enforce anything; treat `polymorph check` output as "the config is
> consistent", not "the guards work". Interfaces and the mapping schema may change without
> notice. Reports of problems are welcome.

polymorph reads a repo's Claude Code agent config (`.agents/settings.json` and the hook
scripts in `.agents/hooks/`) and writes the equivalent hook config for other coding
harnesses, such as ZCode or Gemini CLI. What differs between harnesses (config file
location, tool names, event names, what an exit code means) lives in small declarative TOML
files called mappings, not in code. Python 3.11+, standard library only.

The reason to want this: if you keep guard hooks (a script that refuses `git reset --hard`,
say) for one harness, you would otherwise hand-copy and hand-translate them for every other
one, and the copies drift. polymorph keeps one source of truth and regenerates the rest.

## What it is not

- **Not a security boundary.** The hooks it wires up are usually string matchers over a
  command line. They catch honest mistakes, not a determined or confused agent.
- **Not proof that your guards work.** `polymorph check` is static. It never runs a hook,
  a harness or the network. A clean `check` means the generated files match what the
  mappings say and the scripts exist. It does not mean the harness calls the hook, honours
  a deny, or reads the file you wrote. See [Limits](docs/limits.md).
- **Not verified against live harnesses.** Every shipped mapping says in its `verified`
  field what it actually rests on.
- **Not a way to run untrusted input.** Mappings are data; nothing in one is executed.
  Hook scripts must live under `.agents/hooks/`. See [Concepts](docs/concepts.md#trust-model).

## Install

```sh
nix run github:NireBryce/polymorph -- --help     # not run (the github ref); `nix run .#default` in a clone was
pip install -e .                                  # from a clone (plain `pip install .` works too)
python -m polymorph --help                        # after pip install -e ., or with PYTHONPATH=src
```

Either install works: the built-in mappings and fixtures ship inside the Python package
(`src/polymorph/mappings/`, `src/polymorph/fixtures/`), so a plain `pip install .` can run
`conformance` and `check` from any directory. The editable install is for working on polymorph.

## Five-minute quickstart

You have a repo with `.agents/settings.json` and hook scripts under `.agents/hooks/`, and
you want ZCode to run the same hooks.

```sh
# 1. Say which harnesses to generate for.
mkdir -p .agents/polymorph
printf 'enabled = ["zcode"]\n' > .agents/polymorph/config.toml

# 2. See what would be written, and what has no equivalent. Writes nothing.
python -m polymorph generate --check

# 3. Write .zcode/config.json.
python -m polymorph generate

# 4. Check the repo is consistent. Exit 0 means no errors.
python -m polymorph check
```

Step 2 on a repo with a `permissions` block prints something like:

```
[zcode] would write .zcode/config.json (DRIFT)
  unmapped setting: permissions.allow (1 entries)
```

`unmapped setting` means polymorph found a setting with no cross-harness meaning and did
not translate it. It is reported, never silently dropped. `check` before step 3 fails with
`generated-drift` because the file does not exist yet; after step 3 it passes.

Commit `.zcode/config.json` and run `polymorph check --strict` in CI so the two never
disagree. Details: [Getting started](docs/getting-started.md), [CI](docs/ci.md).

## Commands

| Command | What it does |
|---|---|
| `generate` | Write each enabled harness's config from `.agents/settings.json`. `--check` writes nothing and exits 1 on drift. |
| `check` | Static analysis for CI: scripts exist and are executable, mappings valid, generated files current, fixtures replay. Text, JSON or SARIF. |
| `adapter` | The runtime shim a generated config calls: translates a native hook payload, runs your script, translates the answer back. |
| `validate` | Check one mapping file against schema 1. |
| `conformance` | Replay the shipped fixtures through the adapter. |
| `discover init` / `log` / `summary` / `draft` / `reload-check` | Record what an unmapped harness sends to hooks and draft a mapping from it. |

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
PYTHONPATH=src python3 -m unittest discover -s tests
```

## License

Apache-2.0; see [LICENSE](LICENSE).
