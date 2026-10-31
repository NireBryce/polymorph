---
name: ship
description: How to land work on main through a branch, a PR, one confirmation, the merge, and deleting the branch.
when_to_use: A bare "push", "ship it", "land this" or "merge this" -- any ask to get changes onto main.
---

# Landing work on main

## Applies to

An ask to get changes onto `main`: a bare "push", "ship it", "land this",
"merge this". Not for other git work:

| ask | what to do |
|---|---|
| "push this branch" | `git push` it. No PR. |
| "open a PR" (no merge ask) | Open it and stop. |
| "commit this" | Commit. Pushing was not asked for. |
| another branch named outright | Push directly there. |

If unsure whether an ask means `main`, ask. `main` never takes a direct
push from an agent; this flow is how a change reaches it.

**One** confirmation, asked once CI is green, covers both actions: "merge,
and delete the branch afterward?" On yes, both happen in the same turn.

## 0. Is it green locally?

```sh
git fetch origin
just preflight
```

If it passed locally and CI is red, the step it missed belongs in
`preflight` too (CI runs only that recipe).

## 1. Branch, commit, open the PR

In your own worktree (skill `use-a-worktree`):

```sh
git switch -c feat/<thing> origin/main
git add <paths>          # name them; never a bare `git add -A`
git commit -F - <<'MSG'
feat: <what changed>

<why, and what was verified>

Co-Authored-By: Claude
MSG
git push -u origin feat/<thing>
gh pr create --base main --title "<subject>" --body-file <file>
```

- Branch and first line start with `feat/`, `fix/`, `docs:`, `chore:`.
- The message says what this commit contains. Use a quoted heredoc or a
  file so backticks are not executed.
- PR body in the sections of `.github/PULL_REQUEST_TEMPLATE.md`, ending
  with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Say which rung you reached (unit tests, ran the CLI, live harness).

## 2. Wait for CI, then ask

```sh
just ci-wait <n>
```

It prints `<sha7> <status> <conclusion>` and the PR's mergeStateStatus, and
exits 0 only when the run on the PR's current head succeeded and the PR is
`CLEAN`. Read the exit status before going on. A red check: `gh run view
<run> --log-failed`, fix, push, wait again. When it exits 0, show the title,
URL, commit list and diff stat, and ask the one combined question. If it
does not, leave the PR open, say so, and stop.

## 3. Merge, then delete -- on yes only

```sh
just ship-land <n>
```

It re-checks what `ci-wait` checks and refuses otherwise, and refuses a PR from
another repository, with a protected head branch (`main`, `master`,
`experimental`, the default branch) or with a base other than the default branch
(`--allow-base BRANCH` for a stacked PR), all before merging. It merges with
`--rebase` (one commit) or `--merge` (several, each message kept), pinned
with `--match-head-commit` to the head it checked, then fast-forwards the
local base branch, removes a worktree on the branch, and deletes the local
branch (`-D` only if it is exactly the verified head) and the remote branch (kept
while another open PR is based on it; one already gone counts as done). Run it from
a checkout other than the worktree being removed; it refuses otherwise. It
prints each step. A failed merge deletes nothing; report it.

It closes no issues. Close the ones the PR resolves by hand and say so; a
closing keyword in a commit or PR body is not how this repo does it.
