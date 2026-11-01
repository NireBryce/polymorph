---
name: review
description: How to review a change to polymorph with its own checks and known failure modes.
when_to_use: Reviewing a PR, branch or commit here, or before saying your own change is done.
---

# Reviewing a change

## Applies to

A PR, branch or commit here, yours or another's, and the last step before
saying "done".

## Run first

1. `git status --short` -- an untracked test or fixture does not exist to
   `nix flake check`.
2. `just preflight`, reading the output rather than the exit status.
3. For a check the change adds or edits: undo the core of the change and
   confirm the check fails for that reason.

## Read for

- **Any path that executes mapping content**: `eval`, `exec`, `subprocess`
  or `shlex`/shell on a value from a mapping, a discovery log or a report;
  an `import` chosen by a mapping field. These are never acceptable.
- **A widened trust boundary**: hook scripts no longer required under
  `.agents/hooks/`, mappings loaded from a new place without `--trust-dir`.
- **A new dependency**: stdlib only.
- **A mapping with `verified` and no live session** behind it.
- **A schema change** without SPEC, validator, fixtures and docs together
  (skill `change-the-schema`).
- **Failing open**: a translation failure must be visible and must not
  become an allow or a deny.
- **A new CLI subcommand** missing from `docs/cli.md`.
- **A log or fixture holding real commands, paths or tokens.**
- **Public text** that narrates what could slip past a guard instead of
  stating what it enforces.

## Saying the outcome

"Unit tests pass", "ran the CLI" and "ran in a live harness" are different
claims. Say which you saw.
