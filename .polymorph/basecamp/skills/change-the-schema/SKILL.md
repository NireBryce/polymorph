---
name: change-the-schema
description: How to change the mapping schema or the canonical config so every place that depends on it changes together.
when_to_use: Adding, renaming or removing a mapping key, a capability, a template, an extractor, a canonical event or a check code.
---

# Changing the schema

## Applies to

Any change to what a mapping file or a coverage table may contain, or to
`Hook`/`Canon`, the canonical payload keys, the adapter's environment
variables, or the codes `polymorph manage check` emits. These
live in several files and drift apart silently.

## What changes together

| Change | Files |
|---|---|
| a mapping key or capability | `src/polymorph/mapping.py` (parse), `validate.py` (unknown keys are errors, so the key must be added), `generate.py`/`adapter.py` (use it), `docs/SPEC.md` "Mapping file", every `src/polymorph/mappings/*.toml` that needs it; for a capability also `probe.py` (`CASES`: which probe route settles it) |
| a canonical payload key | `canonical.py` (`CANON_PATHS`: the adapter places values with it, and `validate.py`'s direct-delivery rule compares against it), SPEC "Mapping file", the fixtures that carry it |
| a canonical event or class | `canon.py`, `reader_claude.py`, `mapping.py` events/tools, SPEC "Canonical config", mappings |
| an extractor or template | `extractors.py` / `generate.py` by name (never from mapping text), `validate.py`'s allowed names, SPEC |
| a `GUARD_*` variable | `adapter.py`, SPEC "Wire protocol", and the hooks that read it (outside this repo: say so in the PR) |
| a check code | `check.py`, SPEC's check table, `docs/cli.md`'s table, `tests/test_check.py` |
| a decision kind or choice | `decisions.py` (`KINDS`), `generate.py` (`hook_mismatches`, how a choice applies), SPEC "Decisions", `docs/cli.md`, `tests/test_decisions.py` |
| fixture shape | `conformance.py`, `src/polymorph/fixtures/**`, SPEC "Fixtures" |
| a coverage-table key or surface kind | `inventory.py` (`parse_coverage`), `src/polymorph/coverage/*.toml`, SPEC "Inventory and coverage tables", `tests/test_inventory.py` |

Then `README.md` and `docs/cli.md` if a command or example changed, and the
`CHANGELOG.md` entry if one exists.

## Rules

- Test first: write the case that shows the new behaviour (and, for the
  validator, the case it must reject), see it fail, then change the code.
- Reject the absence too: when a rule rejects a bad explicit value, check
  what the runtime does when the field is missing -- the default is often
  exactly the value you rejected (`payload_out.deny.exit` defaulted to 0,
  an allow). Write the missing-field case next to the bad-value case.
- Bump `schema` and refuse the old value only for a breaking change;
  adding an optional key is not breaking. Say what a break costs users.
- Anything a mapping can name stays data: dotted paths, `{placeholder}`s and
  names of built-ins. Never add a field whose value is evaluated, executed,
  imported or shell-expanded.
- Run `polymorph manage validate` and `polymorph manage conformance` over every shipped
  mapping, then `just preflight`.
