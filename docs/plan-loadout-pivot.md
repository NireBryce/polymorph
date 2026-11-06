# Plan: from transliteration to loadout management

_Status: proposal, 2026-10-31. Baseline: the README's [scope baseline](../README.md#scope-baseline--2026-10-31) section, added in the same change as this plan. Nothing here is built yet; each phase lands behind its own tests._

## The pivot in one paragraph

polymorph v0 reads a repo's Claude Code agent config and transliterates it into per-harness hook files. The next scope makes it the repo's basecamp for agent configuration — a loadout manager: skills, rules, hooks and memory directives accumulate and are managed there, and an arriving contributor's harness is equipped with its loadout on demand, translating what the repo provides into versions its agent and harness understand and naming what cannot be covered. A loadout is a set of initial conditions meant to be loaded at every agent launch; equipping also hands over an environment contract (agent identity, vault detachment) instead of improvised per-contributor setup. The transliteration core is unchanged; it becomes one slice of loadout management. Untested on harnesses with built-in memory, and likely to stay so; see [Limits](limits.md).

## Guiding constraints

Carried over from [Limits](limits.md) and the trust model, extended to every new slice:

- polymorph never writes outside the repository, never handles secret material, and never executes repo content.
- The env slice in particular is **emit-only**: it prints a contract; applying it belongs to the caller.
- Everything new is static and checked like the rest of the tool (per-phase tests, each against a case the phase should catch).

## Slice 1 — inventory breadth

The reader grows from hooks + settings to the full agent-facing surface, one inventory kind at a time. Each kind declares its treatment:

| Kind | Source | Treatment | Rationale |
|---|---|---|---|
| settings | `.agents/settings.json` | transliterate | shipped |
| hooks | `.agents/hooks/` | transliterate | shipped |
| skills | `.agents/skills/` | discover + report | file-backed; harnesses that read them as files need pointing, not translation |
| rules | `.claude/rules/`, `.agents/rules/` | discover + report | same file-backed pattern |
| memory | `AGENTS.md`, `CLAUDE.md` | discover + report (+ symlink detection) | content is harness-agnostic; the symlinks are the portability mechanism |
| `.claude/` | `.claude/` | discover + report | the source inventory for Claude-family harnesses |

Consequences:

- The canonical config gains per-kind sections; the "unmapped, not dropped" behavior extends per kind — a skill no target harness reads is **reported**, which is the README's founding ethos applied at repo scale.
- The per-harness **gap report becomes first-class**: what harness X honours, and what X will silently ignore even after onboarding.
- Only transliterate kinds get mapping files. Discover-and-report kinds instead need a small declarative **coverage table** per harness (what it honours), which is data, not code — the same character as the mappings.
- The basecamp store (`.polymorph/basecamp/`) holds canonical files — core skills, `AGENTS.md`/`CLAUDE.md` — and symlinks them to where agents expect them; discovery reports the agent-facing path and follows the link to the canonical file.

Phase 1 verification: a fixture repo containing every kind, plus a coverage table that ignores one of them, and a test asserting the gap report names exactly that one.

## Slice 2 — the env contract

- Input: a declarative host-facts file (TOML, same character as the mappings — recommended location `.polymorph/basecamp/env.toml`): the machine key's rendered path, scope flags, the vault socket policy, and per-harness session markers.
- Subcommand: `polymorph env [--harness NAME] [--exec CMD]` — emits the env contract as shell exports (unset the vault socket; point `GIT_SSH_COMMAND` at the machine key; an informational line naming the key and its scope), or runs `CMD` under it.
- **Emit-only**: the key is referenced by path; its contents are never read, printed, or moved. No host mutation, no daemon.
- Harness-generic: the contract keys off harness names from the mappings and host facts, never a hardcoded marker.

Phase 2 verification: emit-shape tests against host-facts fixtures (exact exports), and a test asserting polymorph wrote nothing outside the repository.

## Slice 3 — the equip entrypoint

`polymorph equip --harness NAME`: compose slice 1's gap report, `generate`'s files, and slice 2's env contract into one onboarding run that equips the harness — what was written, what the harness honours, what it will silently ignore, what env it emitted. The README's opening paragraph updates around equipping, and the scope baseline section gains a delivered note.

## CLI shape: two halves

The behaviour splits by audience, not by packaging — one tool, two halves, as
subcommands:

- **`polymorph manage ...`** — the maintainer half, deployed in the repo: its config
  lives under the basecamp directory, its output is committed, its checks run in CI.
  `generate`, `check`, `validate` and `conformance` move under it when slice 3 lands,
  and basecamp-keeping work accumulates here: inventory kinds, coverage tables,
  staleness, upstream review, `adopt`.
- **`polymorph equip --harness NAME`** — the contributor half, run on a repo: it reads
  the basecamp and writes equipped artifacts. One command, not a group — the contributor
  does one thing — and contributor work grows inside it (the gitignore flow, provenance)
  rather than beside it.
- Outside the halves: `adapter` and `env` are launch-time (the per-launch part of the
  loadout definition), and `discover`/`capture` serve a third audience, the harness
  mapper.

The rule that sorts any future command: commands that write the basecamp are maintainer
commands; commands that read the basecamp and write equipped artifacts are contributor
commands; everything else is launch-time or mapping work. The command moves happen with
slice 3, not before — one coherent change, no half-migrated default command.

## Phase order

1 → 2 → 3. Each phase lands behind its own tests and leaves the tool fully usable; no flag day, no half-migrated default command.

## Working across repos: two postures

- **Maintainer posture** — the basecamp is committed: equipped artifacts for the enabled
  harnesses are committed with it, and `check --strict` in CI guards drift. This is the
  README's existing model, extended from hook configs to every equipped artifact.
- **Contributor posture** — equipping someone else's repo: everything equip writes is
  derived and regenerable, so it is gitignored and stays local. Equip detects the posture
  (a committed basecamp with the check in CI) and offers a marked, idempotent
  `.gitignore` section otherwise.

What upstream accepts from a contributor splits by artifact type. A PR adding
transliterated hook configs on a **verified** mapping can be accepted by CI alone
(`check --strict` plus conformance fixtures — no human in the loop). A PR adding
translated **skills** — prose — always needs human review; a wrong translation of a
guard-adjacent skill fails open. Ingest of a contributor's equipped tree is a
maintainer-facing check, not a merge-time hope.

Edits made to equipped artifacts route by what changed: an improvement to what a skill
*says* is a PR against the base file in the basecamp store (upstream re-equips from it);
an equipped copy wrong or stale relative to its base is a bug on the tool, and the
provenance manifest (source path and content hash per equipped artifact, written at
equip time) makes that checkable in CI.

## Companion work

One piece each, tracked beside the slices:

- gitignore flow for equipped artifacts (contributor posture)
- provenance manifest: source path and content hash per equipped artifact
- staleness: `check` warns when an equipped copy's base changed since it was equipped
- upstream review path: CI-accept transliterated loadouts on verified mappings,
  human-review prose

`adopt` (#24) and the Rust rewrite consideration (#25) are filed on the tracker.

## Non-goals

- No secret-material handling: the env contract references paths; it never reads, prints, or moves key contents.
- No daemon, no network calls, no host mutation beyond the repository.
- Not a security boundary: [Limits](limits.md) is unchanged, and the gap report says what a harness honours, not whether the harness enforces it.

## Open questions (answered where slice 1 settled them)

1. Per-kind mapping files vs sections in one file — **answered**: per-kind files. The coverage tables landed as one file per harness; hook mappings stay one file each, and slice 3's per-kind mappings follow the same shape, so each harness × kind keeps its own `verified` granularity.
2. Schema bump for the new kinds — **answered**: none. Hook mappings stay `schema = 1`; the discover-and-report kinds are new files (`coverage/NAME.toml`, itself schema 1 and closed-world), not a schema change.
3. The env facts file convention — **answered as recommended**: `.polymorph/basecamp/env.toml`, now mirroring the mappings and coverage override paths.
4. `AGENTS.md` treatment — **answered**: discover and point, never parse. The inventory reports the path and the resolved symlink target; contents are never opened.
5. Where the basecamp directory standard lives long-term — `.polymorph/basecamp/` for now (outside `.agents/`, so a manager's storage is not read as repo-provided agent config); revisit if a second basecamp manager appears.
