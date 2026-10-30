---
name: record-a-lesson
description: How to add something a session learned the hard way to wiki/agents-lessons-learned.md.
when_to_use: A check passed over a real bug, a tool behaved unlike you expected, or a workaround took more than one try -- before ending the task.
---

# Recording a lesson

## Applies to

Before ending a task in which a test or check passed while the thing it
tests was broken, a tool behaved differently from what you expected and it
cost a try, or getting something to work needed a workaround. Record
without being asked. Not for a bug in this repo's code (that is a fix or
skill `propose-issue`), and not for what the page or `AGENTS.md` already
says: sharpen that line instead.

## How

Add one row to the table in `wiki/agents-lessons-learned.md`:

| Lesson | Rule | Home |
|---|---|---|

- **Lesson**: what went wrong, one line, in words a search would find.
- **Rule**: what to do instead.
- **Home**: where the rule is enforced or stated (a skill, an `AGENTS.md`
  rule, a test, a `just` recipe). A rule only the lessons page states
  reaches nobody: if it has no Home, add one (propose it to the user if the
  Home is a hard rule), or write "none yet".

Keep it to what transfers; the commit log holds the narrative. Public text
only: no exploit narration, no real paths or names.
