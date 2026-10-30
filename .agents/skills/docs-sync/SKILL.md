---
name: docs-sync
description: How to check that a change did not leave a doc stale, and fix it in the same change.
when_to_use: At the end of any change to behaviour, the schema, the CLI, a mapping or the layout.
---

# Keeping docs true

## Applies to

The end of any change. A doc that a change made false is fixed in that
change, not later.

## Where a fact lives

| Fact | Owner | Others point to it |
|---|---|---|
| mapping schema, canonical config, trust rules | `docs/SPEC.md` | `README.md`, `AGENTS.md` |
| CLI subcommands and flags | `docs/cli.md`, `src/polymorph/cli.py` | `README.md` |
| what each module does | `AGENTS.md` architecture table | |
| check codes (`polymorph check`) | `docs/SPEC.md` | |
| which mappings are verified | the `verified` key in `src/polymorph/mappings/*.toml` | never copied into prose |

## Do

1. `git diff --stat`; for each changed area, open its owner above and read
   the claims it makes.
2. `grep -rn` the old name, flag, key or path across `*.md`, `*.toml`,
   `.github/`.
3. Do not copy counts, versions or `verified` dates into prose; name the
   file or command instead. A copy drifts silently.
4. `just docs` for the mechanical part (links, skills listed, every CLI
   subcommand documented, banner).
5. Say what you checked, and what you could not (what only a live harness
   can show).
