---
name: use-a-worktree
description: How to work in an isolated git worktree instead of a checkout another session may share.
when_to_use: Before the first git command that branches, commits, merges, or checks out -- not for read-only work.
---

# Working in your own worktree

## Applies to

The first time in a session you are about to run a git command that changes
what is checked out or where a branch points (`switch -c`, `commit`,
`merge`, `branch -d`). Not for read-only work. Stay in the current checkout
when the user says to, or when you already made a worktree for this branch.

## Why

Two sessions sharing one checkout see each other's checkouts and commits
mid-task: files revert, `git branch -d` fails because the other session has
the branch checked out. A worktree has its own directory and `HEAD` and
shares `.git`.

## How

```sh
git -C <repo> fetch origin
git -C <repo> worktree add <scratch>/wt-<thing> -b feat/<thing> origin/experimental
```

Branch from `origin/experimental`, where work lands by default (skill
`ship`); the promotion to `main` starts there too (skill `promote-to-main`).
Branch from `origin/main` only when the user asks for a change based on
`main`.

Name the branch you will ship. Check `git status -sb` and `pwd` before
anything that changes state: a tool that resets the working directory puts
you back in the shared checkout. Prefer `git -C <path>` and absolute paths.
`git add` a new file before `nix flake check`, or the flake does not see it.

A worktree does not isolate a harness's workspace config: one writer at a
time for its hook wiring (`.zcode/config.json`, say). A running session
keeps the wiring it started with, so another session's write lands on disk
unseen; diff the file before trusting a run's setup.

Then read what the other worktrees are doing and say what this one will
touch (skill `agent-log`): `just agent-log read`, then
`just agent-log add claim --paths PATH "what you are changing"`.

## Afterwards

Once landed (skill `ship`): `git worktree remove <path>`, then
`git worktree prune`. Dropping the worktree without landing: write
`just agent-log add abandon "dropped, and why"` first; the entries stay in
the log, and the claim closes. If git state does not match your last action, read
`git reflog` before concluding anything is broken.
