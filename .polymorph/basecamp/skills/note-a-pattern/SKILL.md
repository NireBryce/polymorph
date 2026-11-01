---
name: note-a-pattern
description: Propose, in one line, that something you just did is a reusable agent-assisted development pattern for this repo.
when_to_use: At the end of a task, when what you did worked and looks likely to recur here.
---

# Noting a pattern

## Applies to

The end of a task, when something you did worked and looks likely to recur:
a way of verifying, a workflow, a guard shape, a recovery from a tool limit.
One test to start from: would a person setting up a different repo want to
copy it? Not for: a mistake (that is a lesson, `record-a-lesson`); a
mechanism worth bringing in from another repo (`port-agent-tooling`); a
repeated command (script it as a `just` recipe); a bug (`propose-issue`); a
naming decision (`name-a-thing` records that in `docs/vocabulary.md`
itself).

## The rule

Say so in the final report to the user, in one line naming the pattern and
the page it would go on — `wiki/patterns.md`, as a new row or a new section.
Do not write the page unasked: the wiki is written on a yes, the same way
`propose-issue` files.

On a yes, the pattern lands as a row on `wiki/patterns.md` — what it is,
why it exists, what it costs, and the skill, check or hook that carries it —
linked from where it is enforced. The page indexes; it does not restate.

## Fixture cases

- Flags: you recovered from a flaky check by re-running one recipe, the
  recovery is not written down anywhere, and it looks repeatable — one line
  proposing it for `wiki/patterns.md`.
- Does not flag: you hit a tool bug and worked around it — that is a lesson
  row via `record-a-lesson`, not a pattern.
