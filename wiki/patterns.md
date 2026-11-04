# Patterns: how this repo is developed with agents

Adapted from nixos-configs issue #618, which planned this for that repo
before polymorph's pivot took over the role. This page is the index over the
repo's agent-assisted development patterns: each row names the skill, check,
hook or page that carries it, and does not restate it. When you notice a new
one, skill `note-a-pattern` proposes it in one line. Mistakes are not
patterns — they go to [agents-lessons-learned.md](agents-lessons-learned.md).

| Pattern | What it is | Lives in |
|---|---|---|
| Say which rung you reached | unit tests, ran the CLI, or a live harness — never claim more than you saw | [AGENTS.md](../AGENTS.md) hard rules |
| A check is shown to fail before it is trusted | every checker ships with a test on a bad input it must flag | [tests/test_docs.py](../tests/test_docs.py) |
| Lessons have a Home | a recorded mistake's rule lands where it is enforced, or it reaches nobody | [agents-lessons-learned.md](agents-lessons-learned.md), skill `record-a-lesson` |
| One worktree per task | branching work never happens in the shared checkout | skill [use-a-worktree](../.polymorph/basecamp/skills/use-a-worktree/SKILL.md) |
| One confirmation to land | CI is waited on, then a single yes covers merge and branch deletion | skill [ship](../.polymorph/basecamp/skills/ship/SKILL.md), `just ci-wait`, `just ship-land` |
| Docs are checked mechanically, synced in the same change | `just docs` verifies skills, links, CLI coverage, banner and home paths; a change that stales a doc fixes it | `just docs`, skill [docs-sync](../.polymorph/basecamp/skills/docs-sync/SKILL.md) |
| Vocabulary is fixed and recorded | the pivot's words have one-job definitions; rejected names are recorded with their reasons | [docs/vocabulary.md](../docs/vocabulary.md), skill [name-a-thing](../.polymorph/basecamp/skills/name-a-thing/SKILL.md) |
| The basecamp is a store | canonical copies live in `.polymorph/basecamp/` and the agent-facing paths are symlinks into it | [.polymorph/basecamp/README.md](../.polymorph/basecamp/README.md) |
| Mappings are data | nothing in a mapping is executed; hook scripts live under `.agents/hooks/` | [SPEC](../docs/SPEC.md) trust model |
| Redact by allowlist | keep only named structural values; everything else becomes `{type, length}` | [SPEC](../docs/SPEC.md) privacy |
| Port, don't copy | tooling from other repos is judged at the mechanism level, then ported with rewritten nouns and re-earned evidence | skill [port-agent-tooling](../.polymorph/basecamp/skills/port-agent-tooling/SKILL.md) (lineage: nixos-configs `extract-agent-tooling`, launchpad PR #1) |
| Whole-repo review by read-only subagent sweeps | two agents sweep disjoint module groups against the review checklist; every finding is verified with a failing test before a fix, and design-level items become issues | skill [review](../.polymorph/basecamp/skills/review/SKILL.md) (whole-repo sweeps) |
| Post-landing probe | after landing a batch, re-read the landed diff with fresh eyes and hand-run the runtime boundary through the CLI (a bad mapping through `adapter`, a new finding through `check`) — the unit suite cannot show what a hand-run can, and on the 2026-11 hardening batch this was what found the two fail-open fixes | skill [review](../.polymorph/basecamp/skills/review/SKILL.md) (before saying done) |
| The banner stays honest | the project is experimental and unvetted; cost is stated, not implied | [README](../README.md) notice and [cost and responsibility](../README.md#cost-and-responsibility) |

The meta-pattern: when something you did worked and looks likely to recur,
skill [note-a-pattern](../.polymorph/basecamp/skills/note-a-pattern/SKILL.md)
proposes it for this page in one line — the page grows by proposal, not by
unasked writing.
