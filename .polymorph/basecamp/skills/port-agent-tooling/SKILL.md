---
name: port-agent-tooling
description: Decide which agent tooling from another repo is worth porting into this one, then port it without copying its nouns.
when_to_use: Equipping this repo from another repo's skills, hooks, checks, or conventions, or judging a recorded porting candidate.
---

# Porting agent tooling

## Applies to

Bringing agent tooling from one or more source repos into this one — skills,
path-scoped rules, harness hooks and settings, git hooks, agent scripts, doc
conventions, lessons logs, instruction-file sections — and judging a candidate
already recorded for porting. Not for: transliterating this repo's own config
for another harness (that is the tool's own job, `src/polymorph/`); reshaping
this repo into store-and-symlink form (`polymorph manage adopt`); writing a
new skill from scratch (`new-skill`).

## Lineage

Adapted from launchpad's `port-agent-tooling` skill (NireBryce/launchpad,
`skills/port-agent-tooling/SKILL.md`), which carries the full procedure, the
read-only inventory scanner (`inventory.py`, stdlib-only) and a worked example
(nixos-configs → factcheck). This file is the polymorph-shaped version: same
generality test, same port rules, plus what the basecamp model changes. The
scanner is not vendored here; run it from a launchpad checkout.

## The procedure

1. Inventory the sources. Launchpad's `inventory.py` gives per-item
   summaries, repo-noun counts, evidence (dates, `#N`, `§N`), couplings, and
   what CI, git hooks, or settings wire by path.
2. Profile the target — for this repo: the basecamp store
   (`.polymorph/basecamp/`) holds canonical files and symlinks them to the
   agent-facing paths; the checks are `just preflight`, and `just
   self-check` fails on a new gap in this repo's own agent layout. Its agents
   run in Claude Code and ZCode, which read `AGENTS.md` and the skills
   through those symlinks. It has no hook scripts of its own yet (no
   `.agents/settings.json`).
3. Judge each candidate at the mechanism level — launchpad's generality test:
   restate the thing's purpose with every repo noun removed; what survives is
   the portable kernel. Group candidates by coupling (a skill and the check
   enforcing it travel together), and split each into its automatic half
   (hook, check, CI step) and its convention half.
4. Give each candidate a verdict — port / port, adapted / feature / later /
   skip — in a ranked report. **Stop for the user's pick before porting
   anything.**
5. Port, don't copy: the rules below.
6. Say which rung each port reached (AGENTS.md: unit tests, ran the CLI,
   live harness).

## What the basecamp model changes

- A ported content file lands in the store (`.polymorph/basecamp/...`), and
  the agent-facing symlink picks it up. Never create a second copy at a link
  path; a link path is never a second copy.
- What equip issues is what you ported, so re-earn the evidence here: a
  ported hook needs a mapping with fixtures and `verified` text; a ported
  skill or convention needs a check (`just docs`, `tests/docs.py`) or a test
  that fails on the old behaviour.
- Anything a port creates gets named through skill `name-a-thing`; if it is
  a concept, its decision goes in `docs/vocabulary.md`.
- Edits to ported content flow back upstream as PRs against the source's
  base file. Drift between the copy and its base is a bug, not a fork.

## Port rules

Distilled from launchpad's skill, which is the full list:

- Rewrite the nouns, keep the rule, and add a provenance pointer to the
  source.
- Port couplings whole — a skill and the check that enforces it arrive
  together, or neither does.
- Re-earn evidence in the target; evidence in the source does not transfer.
- One layer is not enforcement: pair a git hook with a CI check.
- CI runs the contributors' command, not a copy of its steps.
- Hooks keep their decision form: a must-hold guard denies; an ask is a
  no-op under unattended modes.
- Pre-approved permissions are re-justified, never copied.
- A lesson ports as its rule, in its Home (`wiki/agents-lessons-learned.md`).
- Security analysis stays private to the security issue.
- Never run source content to inspect it: read it. Mappings stay data; hook
  scripts land under `.agents/hooks/` or nowhere.
