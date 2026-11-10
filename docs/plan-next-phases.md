# Plan: the phases after the agent log

_Status: proposal with decisions recorded, 2026-11-10. Nothing here is built unless it says landed. polymorph is experimental and unvetted ([Limits](limits.md)); every phase says what it could break. This plan follows the [loadout pivot plan](plan-loadout-pivot.md) and does not replace it._

This page holds the decisions and the task list, so work can continue without the conversation that produced them. How an agent picks up and lands a task is skill `next-task`; this page does not repeat it.

## Decisions so far

| Date | Decision | By |
|---|---|---|
| 2026-11-10 | The agent log archive keeps `decision`, `gotcha`, `abandon`, and `sops` entries and drops `done` (`git log` has it). | user |
| 2026-11-10 | Agents run on more than one machine, so cross-machine sharing (stage 2) stays on the plan. | user |
| 2026-11-10 | Order: cleanup, then the log trial with the read-only work beside it, then stage 2 ahead of the transliteration refactor. Reasoning in [Order](#order). | agent proposed, user agreed |
| 2026-11-10 | Agent flow lives in the skills and `AGENTS.md`; this page is data (decisions, tasks, tables), not procedure. | user |
| 2026-11-10 | "Say what happened, not what to do" in log entries is a convention, not a rule the script enforces. | recorded in the [style guide](../prototypes/agent-log/style-guide.md) |

Open, waiting on the user:

- Do contributors or agents push to forks rather than the main repository? Stage 2 pushes refs to the upstream repository, which fork contributors cannot do.
- A yes to run the secret-scanning test (task T6), because it pushes to GitHub.

## Where things stand

Landed on `experimental`:

- The agent log, a prototype in `prototypes/agent-log/` ([README](../prototypes/agent-log/README.md), [style guide](../prototypes/agent-log/style-guide.md)): a shared one-line-per-event log for agents in separate worktrees of one clone, archived into the PR, with rules that keep secrets and instruction-shaped text out of it. `AGENTS.md` tells every session to use it.
- The rule that a step repeated the same way is a script, not prose, with room for an agent to tell the user when a skill would be a big gain.

Open GitHub items: #113 (lessons from the agent-log work), #112 (`ci-wait` can report success for the previous head right after a push), #111 (is the agent log a polymorph module or a composable tool?), #108 (split transliteration by artifact kind), #105 (a harness-neutral canonical format), #76 (extracting skills and scripts across repos).

## Order

1. Merge the two small text PRs (T1, T2).
2. In parallel, all cheap or read-only: the log trial (T4), the skills-versus-scripts audit (T11), the survey of what each artifact kind does today (T12), the stage 2 design on paper (done, in [stage-2.md](../prototypes/agent-log/stage-2.md)), and the secret-scanning test (T6).
3. Decide #111 (T7) before building stage 2. Stage 2 pushes to remotes and cannot be undone, which is the strongest argument for making the log its own tool; if it becomes one, extract it first so the push code never sits in polymorph's trust model.
4. Build stage 2 (T8 to T10) only after the trial's exit decision (T5), the scanning-test result (T6), and the #111 decision (T7). The first build pushes nothing.
5. Then the transliteration proposal (T13, T14); build it only after the user accepts the proposal (T15).

Why stage 2 before the refactor: the single-clone log helps agents on one machine, and on separate machines work is seen only through archives in merged PRs, so a conflict shows up at PR time. Stage 2 is the coordination feature; the refactor has no deadline.

## Task list

State is `open`, `blocked`, `in progress`, or `done`. Change a task's state in the same PR that does the work.

| ID | Task | Depends on | Done when | State |
|---|---|---|---|---|
| T1 | Merge #113 (lessons, and two skill lines that carry them) | CI green | merged | open |
| T2 | Merge this plan's PR | CI green | merged | open |
| T3 | Fix `ci-wait` so it cannot pass for a stale head (#112): take the expected sha (a flag, or the PR branch's remote tip) and keep polling while the PR head differs | none | a stub-`gh` test returns the old head first and the new head second, and exit 0 happens only for the new one; the `ship` skill's compare-the-sha line is removed or kept as a backstop | open |
| T4 | Run the agent log as the standard way of working for about five PRs, on every machine in use. Log each surprise as a `gotcha`. Fill the [trial notes](#trial-notes) table after each PR | T1 | five rows in the table | open |
| T5 | Trial exit decision: keep, trim (name the kind or rule), or remove (the style guide's "Undoing this") | T4 | the decision and its reasons are recorded under Decisions | blocked |
| T6 | Test whether GitHub's secret scanning looks at custom refs: in a throwaway private repository, push a ref holding a runtime-built, token-shaped fake, and see whether GitHub flags it; delete the repository after | the user's yes | the result is written under "Unverified" in [stage-2.md](../prototypes/agent-log/stage-2.md) | blocked |
| T7 | Decide #111: polymorph module or composable tool (or stays here) | T4 under way | the decision is recorded on #111 and under Decisions | open |
| T8 | Stage 2, step 1: `sync --dry-run`. Per-agent refs under `refs/agent-log/`, entries as commits with fixed metadata, the rules run over every commit that would be pushed; nothing is pushed | T5, T6, T7 | tests, including a planted violation that refuses the whole run | blocked |
| T9 | Stage 2, step 2: push the agent's own ref and fetch everyone's; first `sync` of a session asks the user; a `pre-push` hook refuses `refs/agent-log/*` unless the script pushes | T8 | tests against a local bare remote; one hand-run against a throwaway repository | blocked |
| T10 | Stage 2, step 3: claims through `sync`, with the overlap report and the back-off rule | T9 | two clones claim the same path within one fetch window and both see the overlap | blocked |
| T11 | D0, the skills-versus-scripts audit: every skill, every step, a verdict (script, keep as skill, ask the user), and the recipe each script row would need | none | the table is in this repository and the "script" rows are filed or done | open |
| T12 | D1, the survey: fill the [survey table](#survey-table) from the code and the three shipped mappings; read-only | none | the table is filled with the file and line each row comes from | open |
| T13 | D2, the proposal for composable transliteration: what a kind declares, how kinds compose in `equip`, how a transform is expressed, what #105 and #76 are assumed to be | T12 | a proposal page the user accepts or rejects | blocked |
| T14 | D3, the name check against [Vocabulary](vocabulary.md) (skill `name-a-thing`) | T13 | the name and the rejected names are recorded in `docs/vocabulary.md` | blocked |
| T15 | D4, build the accepted proposal one kind at a time, each behind a test on a case the kind should catch | T13 accepted, T14 | one task per kind, added here when accepted | blocked |

## How to pick up a task

The flow is in skill `next-task` (and `AGENTS.md`), not on this page: pick an
`open` task whose dependencies are `done`, work in your own worktree, land
through skill `ship`, and update the task's state, the decisions, and any table
the task owns in the same PR. Two tasks need the user, not only an agent: T6
(pushes to GitHub) and T7 (a decision). Do not start either without them.

## Trial notes

Fill one row per PR during T4. A rejected entry counts only if the rule fired on an ordinary sentence.

| PR | Machine | Entries written | Rejections | Of those, false positives | Surprises (as gotcha entries) |
|---|---|---|---|---|---|
| | | | | | |

Token cost to compare against: the estimate before the trial is 3,500 to 5,500 tokens per session, mostly input (reads at session start and before editing a shared path, 6 to 10 writes).

## Survey table

For T12. One row per artifact kind; every cell names the file it comes from.

| Kind | Source in a repo | What claude reads | What zcode reads | What gemini reads | What the code does today (translate, link, report) | Where `equip` or `generate` hard-codes this kind |
|---|---|---|---|---|---|---|
| settings | `.agents/settings.json` | | | | | |
| hooks | `.agents/hooks/` | | | | | |
| skills | `.agents/skills/` | | | | | |
| memory files | `AGENTS.md`, `CLAUDE.md` | | | | | |
| rules | `.claude/rules/`, `.agents/rules/` | | | | | |

Today only hooks go through mappings (`generate.py`). Skills, memory files, and rules are moved into the basecamp store and symlinked by `adopt.py`, `links.py`, and `equip.py`, and the gap report ([Vocabulary](vocabulary.md)) names what a harness does not read.

## Phase B: what the trial could break

A rule with a false positive blocks a ship until the entry is rewritten. The rules are heuristics, so a leak is still possible, and the README says so. Exit by keep, trim, or remove; a removal is a normal outcome.

## Phase C: what stage 2 could break

A pushed line cannot be recalled, so this is the first step that cannot be undone. Fork contributors are left out. GitHub's behaviour with custom refs is unverified until T6. The full design, and what was deliberately not designed, is in [stage-2.md](../prototypes/agent-log/stage-2.md).

## Phase D: what the refactor could break

`generate.py` and `equip.py` are on the safety path; a wrong mapping can fail open, and a refactor can change what `equip` writes for repositories that use it today. Constraints that stay: mapping content is data, never code; transforms are named built-ins in `src/`; the trust boundaries in [SPEC](SPEC.md) do not widen. Nothing is built until the survey is read and the proposal accepted. If the survey shows no current harness needs a transform for skills or rules, "keep linking" is a valid result.
