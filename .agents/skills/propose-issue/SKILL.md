---
name: propose-issue
description: How to propose a GitHub issue for a real bug found in this repo while working on something else.
when_to_use: You noticed a real bug, gap or stale doc here that is outside the current task.
---

# Proposing an issue

## Applies to

A genuine problem in **this repository** (a bug, a missing test for a
failure that can happen, a wrong doc) noticed while doing something else.
File only in `NireBryce/polymorph`; any other project only when the user
names it in their own message. A list of approved tasks does not count.

## Before proposing

- Search: `gh issue list -R NireBryce/polymorph --search "<keywords>" --state all`.
- Reproduce it, or say plainly that you did not.

## Propose, then file on a yes

Show the user a title and body, and ask. Body:

```
## What happens
<observed, with the command and its output>

## Expected
<what should happen, and why>

## Where
<file:line or doc section>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Then `gh issue create -R NireBryce/polymorph --title ... --body-file ...`.
Grep the draft for `owner/repo#123` shapes first: GitHub autolinks them and
pings that project. Public text stays mechanical: what is wrong and how to
see it. For a security weakness, describe the fix needed, not what could
get past the check; the user decides where the rest goes.
