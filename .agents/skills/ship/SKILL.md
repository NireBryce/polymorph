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
gh pr checks <n> --watch
gh pr view <n> --json mergeStateStatus --jq .mergeStateStatus
```

Only `CLEAN` is ready. A red check: `gh run view <run> --log-failed`, fix,
push, wait again. When ready, show the title, URL, commit list and diff
stat, and ask the one combined question. If not ready, leave the PR open,
say so, and stop.

## 3. Merge, then delete -- on yes only

```sh
gh pr merge <n> --rebase      # --merge for several commits, keeping each message
git switch main && git pull --ff-only
git branch -d feat/<thing>
git push origin --delete feat/<thing>
```

Not `--delete-branch`: it removes only the remote branch. A failed merge
deletes nothing; report it.
