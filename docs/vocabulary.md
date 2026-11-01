# Vocabulary

The words this project uses for the scope pivot described in the
[loadout pivot plan](plan-loadout-pivot.md). Decided 2026-10-31; use them as
written in docs, GitHub issues, PR bodies and commit messages so the prose
stays coherent. Until a slice lands, everything here describes a proposal.

## The words

- **equip** — the verb: what polymorph does — equip a harness with its
  loadout. The subcommand, when slice 3 lands, is `polymorph equip --harness
  NAME`. polymorph is the subject: "polymorph equips the harness", never the
  passive "the harness is onboarded".
- **loadout** — what equipping hands over: a set of initial conditions meant
  to be loaded at every agent launch. Per harness ("the zcode loadout for
  this repo"): the written files, the pointed-at files, the env contract and
  the gap report. Untested on harnesses with built-in memory, and likely to
  stay so; see [Limits](limits.md#built-in-memory).
- **basecamp** — the repo's store for its core agent files: skills, `AGENTS.md`/`CLAUDE.md`,
  rules and the like. Canonical copies live in the basecamp, and the paths agents expect
  are symlinks into it. The directory is `.polymorph/basecamp/` for now — outside `.agents/`,
  so harnesses and inventory tools do not read a manager's storage as repo-provided agent
  config. The directory is named for the concept, not the tool: polymorph is one basecamp
  manager among potential others. Maintainers keep the basecamp; arriving harnesses are
  equipped from it.
- **store** — the sense in which the basecamp is a store, the nix-store sense: the one
  managed place where a file's real copy lives; every reference to it lives elsewhere.
  The references are the agent-facing paths — `AGENTS.md` at the root, `.agents/skills/`,
  and the like — each a symlink pointing into `.polymorph/basecamp/`, so a link path is
  never a second copy, an edit made at a link lands on the store's copy, and every
  harness reading any link sees the same bytes.
- **loadout manager** — polymorph's role in the pivot: the tool that keeps a
  repo's basecamp and equips an arriving contributor's harness with its
  loadout on demand.
- **onboarding run** — the same event seen from the agent's side: an agent
  arrives, is equipped, and is running with the repo's intent. Keep it for
  prose about the agent's experience; the command is `equip`.
- **gap report** — the part of the loadout that says what a harness honours
  and what it will silently ignore even after equipping.
- **coverage table** — the per-harness data file (`coverage/NAME.toml`, same
  character as the mappings) that says what a harness honours of the
  discover-and-report kinds; a kind the repo provides but the table does not
  cover is a gap. Not the mapping's `[capabilities]` flags, which say how a
  hook behaves.
- **equipped artifact** — anything polymorph writes or points at when
  equipping a harness, as opposed to files the repo's authors maintain by
  hand. Equipped artifacts record where they came from, so an edit made in
  one can be routed upstream.
- **transliteration** — unchanged: turning config written for one harness
  into files another harness reads and honours. On demand for contributors;
  one slice of loadout management, not a synonym for it.

## Retired words

- **workshop** — the placeholder the pivot carried until it was named; it
  survives in history only.
- **kit** — replaced by loadout. If you reach for kit, write loadout.
- **issue** — briefly the verb ("polymorph issues the loadout"), replaced by
  equip before anything shipped. In this repo it stays a GitHub noun: file an
  issue.
- **armory** — place-name candidate for where loadouts are stocked; rejected.
- **quartermaster, outfitter, loadmaster** — role-noun candidates, rejected:
  they do not say what they mean.
- **spawn point** — rejected: "spawn" does not survive translation out of
  gaming English, and it names a place.
- **brief** — the gap report already says it.
