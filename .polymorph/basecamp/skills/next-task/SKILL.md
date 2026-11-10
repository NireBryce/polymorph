---
name: next-task
description: How to pick up and land a task from the plan's task list, and keep the list true.
when_to_use: Starting a session with no named task, being asked what is next or to work the plan, or finishing a task that has a row in the plan.
---

# Working the plan's task list

## Applies to

The task table in `docs/plan-next-phases.md`: a session that starts without a
named task, a request such as "what's next" or "work the plan", and the end of
any task that has a row there. When the user names a task, do that task, and
still update its row if it has one. Not for work with no row: do not invent
rows; propose a new one to the user.

## Pick

1. Read the plan's "Decisions so far" and its task table. A decision there
   outranks your memory of the conversation that made it.
2. Run `just agent-log read`. An open claim on a task's paths means another
   agent has it; leave it unless the claim is `[stale]`, and then ask the user.
3. Choose a task whose state is `open` and whose "Depends on" tasks are all
   `done`. A task marked as needing the user (a decision, or an action that
   pushes to a remote) is asked about, never started.
4. If two tasks qualify, take the one that unblocks the most others, and say
   why in one line.

## Do

1. Work in your own worktree (skill `use-a-worktree`) and claim the paths you
   will touch (skill `agent-log`).
2. Do what the row's "Done when" says, no more. If the task turns out to be
   several, split the row in the same PR and do the first part.
3. Say which rung you reached (`AGENTS.md`, "Say which rung you reached").

## Land

Through skill `ship`. In the same PR:

- set the row's state to `done`, and change each dependent row that is now
  unblocked from `blocked` to `open`;
- add a row to "Decisions so far" if the task decided something;
- fill any table the task owns (the trial notes, the survey table);
- fix any doc the change made stale (skill `docs-sync`).

If you stop without finishing, leave the row `open` and write `abandon` in the
agent log with what you learned, so the next agent starts from it.
