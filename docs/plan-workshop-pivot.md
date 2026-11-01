# Plan: from transliteration to the workshop

_Status: proposal, 2026-10-31. Baseline: the README's [scope baseline](../README.md#scope-baseline--2026-10-31) section, added in the same change as this plan. Nothing here is built yet; each phase lands behind its own tests._

## The pivot in one paragraph

polymorph v0 reads a repo's Claude Code agent config and transliterates it into per-harness hook files. The next scope makes the goal bigger and plainer: an agent arriving at a repository runs polymorph once and comes away running with everything that repo's agent config intended for it — the guard rails, the harness-specific files, the gaps named — and with its environment (agent identity, vault detachment) emitted as a contract rather than improvised. The transliteration core is unchanged; it becomes one slice of that.

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
- Only transliterate kinds get mapping files. Discover-and-report kinds instead need a small declarative **capability table** per harness (what it honours), which is data, not code — the same character as the mappings.

Phase 1 verification: a fixture repo containing every kind, plus a capability table that ignores one of them, and a test asserting the gap report names exactly that one.

## Slice 2 — the env contract

- Input: a declarative host-facts file (TOML, same character as the mappings — recommended location `.agents/polymorph/env.toml`): the machine key's rendered path, scope flags, the vault socket policy, and per-harness session markers.
- Subcommand: `polymorph env [--harness NAME] [--exec CMD]` — emits the env contract as shell exports (unset the vault socket; point `GIT_SSH_COMMAND` at the machine key; an informational line naming the key and its scope), or runs `CMD` under it.
- **Emit-only**: the key is referenced by path; its contents are never read, printed, or moved. No host mutation, no daemon.
- Harness-generic: the contract keys off harness names from the mappings and host facts, never a hardcoded marker.

Phase 2 verification: emit-shape tests against host-facts fixtures (exact exports), and a test asserting polymorph wrote nothing outside the repository.

## Slice 3 — the workshop entrypoint

`polymorph onboard --harness NAME`: compose slice 1's gap report, `generate`'s files, and slice 2's env contract into one onboarding run with a summary — what was written, what the harness honours, what it will silently ignore, what env it emitted. The README's opening paragraph updates around onboarding, and the scope baseline section gains a delivered note.

## Phase order

1 → 2 → 3. Each phase lands behind its own tests and leaves the tool fully usable; no flag day, no half-migrated default command.

## Non-goals

- No secret-material handling: the env contract references paths; it never reads, prints, or moves key contents.
- No daemon, no network calls, no host mutation beyond the repository.
- Not a security boundary: [Limits](limits.md) is unchanged, and the gap report says what a harness honours, not whether the harness enforces it.

## Open questions (recommendations inline)

1. Per-kind mapping files vs sections in one file — recommended: per-kind files, so each harness × kind keeps its own `verified` granularity.
2. Schema bump for the new kinds — recommended: none. Hook mappings stay `schema = 1`; the discover-and-report kinds are new files, not a schema change.
3. The env facts file convention — recommended: `.agents/polymorph/env.toml`, mirroring the existing mappings override path.
4. `AGENTS.md` treatment — recommended: discover and point, never parse. The content is written for the agent, not the harness.
