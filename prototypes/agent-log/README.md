# agent-log (prototype)

A shared, one-line-per-event log for agents in separate worktrees of one
clone: claims, finished work, decisions, surprises, dropped work, sops
changes. This is a prototype. It is not part of polymorph's product, nothing
here promises compatibility, and its rules are heuristics that lower the
chance of a leak without proving there is none.

| File | What it is |
|---|---|
| `agent_log.py` | the tool: `add`, `read`, `archive`, `prune`, `check`, `flag`, `resolve`; run as `just agent-log ARGS` or `python3 prototypes/agent-log/agent_log.py ARGS` |
| `style-guide.md` | the line format, what to write, the guards, what to do when something got through, and how to undo all of it |
| `../../tests/test_agent_log.py` | its unit tests; they run in `just test` so the prototype does not rot |

The committed archive it writes is `wiki/agent-log/BRANCH.log`, with flags
in `wiki/agent-log/flags/`. `just agent-log-check` (part of `just preflight`)
re-runs its rules over both.

## How it is built

- Entries carry an id (a hash of the line), and everything refers to entries by
  id: claims, flags, redaction, `read`. Nothing points at a line number.
- The live log (in the git common dir) and the archive are only appended to,
  with one write per call. `archive` copies and never rewrites a live file;
  `read` hides archived ids; `prune` deletes a whole live log once its
  archivable entries are archived.
- A flag or a resolution is one file per entry in `wiki/agent-log/flags/`, so
  there is no shared file to update.
- Every command checks everything before it writes anything.
- A change to a sops file is a `sops` entry: a verb and a path, no text.
- The archive keeps `decision`, `gotcha`, `abandon` and `sops` entries. `done`
  stays in the live log; `git log` has it.

## Skills and files that reference it

If the tool moves, changes its commands, or is removed, update or remove
each of these in the same change (skill `docs-sync`):

| Where | What it says about the log |
|---|---|
| skill `agent-log` | the whole procedure: when to read, claim, record, release, abandon and archive |
| skill `use-a-worktree` | read the log and claim paths on entry; write `abandon` when dropping a worktree |
| skill `ship` | archive the worktree's entries before the commit, and `git add` the archive file |
| skill `review` | read archived lines in a diff by eye; whole-repo passes list `wiki/agent-log/flags/` and run `just agent-log check` |
| skill `docs-sync` | the row naming this directory as the owner of the line format and guard rules |
| `.polymorph/basecamp/AGENTS.md` | the "log your work" hard rule, the `just agent-log` command line, the `prototypes/` and `wiki/` architecture rows, the skills-table row |
| `wiki/patterns.md` | the "shared log of what happened" row |
| `wiki/agents-lessons-learned.md` | the row about a test hook that fixed the clock |
| `justfile` | the `agent-log` and `agent-log-check` recipes, and `agent-log-check` in `preflight` |
| `tests/test_run_recipe.py` | the preflight dependency list |

## What it does not do

- **Heuristic guards.** A short password written as a plain word passes, and
  so does a value in a path that exists. A line that mentions sops, a secret or
  a key is held to letters only, which blocks values but also blocks normal
  sentences with digits or slashes; rewrite them.
- **A race on `prune`.** Deleting a live log while its worktree appends to it
  can lose the append. Prune only a worktree that is gone.
- **No locks.** Claims are advisory.
- **One clone.** Nothing moves a log between clones or machines.
- **`--redact` is cosmetic.** The old text stays in git history; rotation is
  the remedy.

## Where it is going

- **Stage 2 (across machines and clones), only after this has been used:**
  one git ref per agent under `refs/agent-log/`, a `sync` that re-runs the gate
  before it pushes and refuses the whole push on a failure, fixed commit
  metadata, advisory claims. A pushed line cannot be recalled, so this stage
  is the first one that cannot be undone, and it may belong in its own repo.
  Whether the log is a polymorph module or a separate tool is issue #111.
- **Keep it extractable:** no imports from polymorph; the archive path and
  the forbidden words belong in flags or a small config file, not in code.
