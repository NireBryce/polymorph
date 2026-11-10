# Plan: the phases after the agent log

_Status: proposal, 2026-11-10. Nothing here is built unless it says landed. polymorph is experimental and unvetted ([Limits](limits.md)); every phase below says what it could break. This plan follows the [loadout pivot plan](plan-loadout-pivot.md) and does not replace it._

## Where things stand

Landed on `experimental`:

- The agent log, a prototype in `prototypes/agent-log/` ([README](../prototypes/agent-log/README.md), [style guide](../prototypes/agent-log/style-guide.md)): a shared one-line-per-event log for agents in separate worktrees of one clone, archived into the PR, with rules that keep secrets and instruction-shaped text out of it. Every session is told to use it (the "log your work" rule in `AGENTS.md`).
- The rule that a step repeated the same way is a script, not prose, with room for an agent to tell the user when a skill would be a big gain.

In review or open:

| Item | State |
|---|---|
| #113 | lessons from the agent-log work, and two skill lines that carry them |
| #112 | `ci-wait` can report success for the previous head right after a push |
| #111 | decide: is the agent log a polymorph module or a composable tool? |
| #108 | investigate: split transliteration by artifact kind into composable pieces |
| #105, #76 | a harness-neutral canonical format; extracting skills and scripts across repos |

## Phase A: cleanup (done, pending #113)

The stale local log is pruned, the `ci-wait` gap is filed, and three lessons are recorded with Homes where a skill could carry them. Exit: #113 merged.

## Phase B: use the agent log before building more

Run it as the standard way of working for the next few PRs, and change nothing about it unless a trial shows a problem.

- **Watch:** how often an entry is rejected and why (a rule that rejects ordinary sentences is a bug, not a lesson for the agent); how many tokens a session spends on reads and writes (estimate before the trial: 3,500 to 5,500 per session, mostly input); whether anyone reads another agent's claim and changes course.
- **Record:** each surprise as a `gotcha` entry, so the trial's findings land in the archive it is testing.
- **Exit:** after about five PRs, decide one of keep as is, trim (drop a kind or a rule), or remove (the "Undoing this" list in the style guide says how). A removal is a normal outcome.
- **Could break:** a rule with a false positive blocks a ship until the entry is rewritten; the rules are heuristics, so a leak is still possible, and the README says so.

## Phase C: decisions that wait on evidence

- **#111, module or tool.** Decide when a second repo wants the log, or when cross-machine work starts. Until then it stays a prototype and stays extractable (no imports from polymorph, configuration in flags).
- **Cross-machine sharing (stage 2).** One git ref per agent, a `sync` that re-runs the rules before it pushes. A pushed line cannot be recalled, so this is the first step that cannot be undone, and fork contributors would be left out. Do it only if agents run on more than one machine. Before relying on GitHub's secret scanning for custom refs, test whether it sees them, against a throwaway private repository, with the user's yes.
- **The archive's scope.** The archive keeps `decision`, `gotcha`, `abandon`, and `sops` entries and drops `done`, because `git log` has it. The user confirmed on 2026-11-10 that dropping `done` is fine; widen it only if the trial shows a gap.

## Phase D: composable transliteration (#108)

The earlier thread, and the larger refactor. Today only hooks go through mappings; skills, `AGENTS.md`, and rules are moved and symlinked by `equip`, `adopt`, and `links.py`. The question is whether each artifact kind should get its own transliterator that composes with the others.

1. **Survey, read-only.** For each kind (settings, hooks, skills, memory files, rules): where the source lives, what each harness reads, what the code does today (translate, symlink, or report), and what the [gap report](vocabulary.md) says. Output: one table, no code.
2. **Proposal.** From the table: what a kind declares (source, per-harness target, transform or link), how kinds compose in `equip`, and how a gap is attributed to a kind. Constraints stay: mapping content is data, never code; transforms are named built-ins in `src/`; the trust boundaries in [SPEC](SPEC.md) do not widen.
3. **Name check.** A new concept needs a name that fits [Vocabulary](vocabulary.md) (skill `name-a-thing`).
4. **Relation to the open issues.** #105 asks whether Claude's settings should stay the canonical format; #76 asks about extracting skills across repos. The survey informs both; the proposal should say what it assumes about each.
5. **Build only after the proposal is accepted,** one kind at a time, each behind a test on a case the kind should catch.
- **Could break:** `generate.py` and `equip.py` are on the safety path; a wrong mapping can fail open. Nothing in this phase starts until the survey is read.

### D0: the skills-versus-scripts audit

The script rule left a pass open: which skill steps are still numbered shell commands that should be scripts, and which scripts would be better as skills. Output: a table of skill, step, verdict (script, keep as skill, ask the user), and a note for each "script" row naming the recipe it would need. It can run beside Phase B and needs no new tooling.

## Order

A, then B and D0 in parallel (B is observation, D0 is reading), then D's survey. C waits for its triggers. If Phase B ends in removal, D is unaffected.

## Questions for the user

1. D first, or B first with D waiting?
2. Do agents here run on more than one machine? If not, stage 2 is off the plan.
