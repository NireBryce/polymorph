# agent-log (prototype)

A shared, one-line-per-event log for agents in separate worktrees of one
clone: claims, finished work, decisions, surprises, dropped work. This is a
prototype. It is not part of polymorph's product, nothing here promises
compatibility, and its rules are heuristics that lower the chance of a leak
without proving there is none.

| File | What it is |
|---|---|
| `agent_log.py` | the tool: `add`, `read`, `archive`, `check`, `flag`, `resolve`; run as `just agent-log ARGS` or `python3 prototypes/agent-log/agent_log.py ARGS` |
| `style-guide.md` | the line format, what to write, the guards, what to do when something got through, and how to undo all of it |
| `../../tests/test_agent_log.py` | its unit tests; they run in `just test` so the prototype does not rot |

The committed archive it writes is `wiki/agent-log/BRANCH.log`, and
`just agent-log-check` (part of `just preflight`) re-runs its rules over it.

## Skills and files that reference it

If the tool moves, changes its commands, or is removed, update or remove
each of these in the same change (skill `docs-sync`):

| Where | What it says about the log |
|---|---|
| skill `agent-log` | the whole procedure: when to read, claim, record, release, abandon and archive |
| skill `use-a-worktree` | read the log and claim paths on entry; write `abandon` when dropping a worktree |
| skill `ship` | archive the worktree's entries before the commit, and `git add` the archive file |
| skill `review` | read archived lines in a diff by eye; whole-repo passes run `just agent-log check --recent-days 30` |
| skill `docs-sync` | the row naming this directory as the owner of the line format and guard rules |
| `.polymorph/basecamp/AGENTS.md` | the "log your work" hard rule, the `just agent-log` command line, the `prototypes/` and `wiki/` architecture rows, the skills-table row |
| `wiki/patterns.md` | the "shared log of what happened" row |
| `wiki/agents-lessons-learned.md` | the row about a test hook that fixed the clock |
| `justfile` | the `agent-log` and `agent-log-check` recipes, and `agent-log-check` in `preflight` |
| `tests/test_run_recipe.py` | the preflight dependency list |

## Known problems

Found by a review of the first version. None is fixed yet.

- **Flags point at line numbers.** A later `archive` into the same branch
  re-sorts the file, so `resolve --redact` can blank a different line while the
  flagged one stays, and `check` then passes.
- **`resolve` and `flag` take the archive name unchecked.** `resolve
  ../x/NAME 1 note --redact` can rewrite a `*.log` file outside the archive.
- **`resolve --redact` with a note the rules reject** still redacts the line,
  writes no `resolved` entry, and prints "nothing written".
- **`archive` rewrites live files**, so an `add` that lands in between can be
  lost. `flag` has the same read-modify-write race.
- **`$USER` and `$LOGNAME` are forbidden words**, so `USER=runner` on a CI
  runner rejects "runner" and `USER=user` rejects "the user".
- **Guard gaps:** `pw`, `pass`, `passphrase`, a UUID after "api key", a
  lowercase-and-digits token, and anything in `paths=` pass.
- **False positives:** `->`, `;`, and lines opening with `use-a-worktree`,
  `open claims`, `set up`, or `read` are rejected.
- **A malformed timestamp** such as `2026-13-45T10:00Z` crashes `read`.

## Where it is going

- **Stage 1 (single clone):** a stored entry id instead of line numbers, an
  append-only live log and archive, one file per flag, a `sops` kind with a
  verb and tracked paths and no free text, `paths=` limited to tracked files,
  and a narrower rule set (no imperative-start rule, no `$USER`).
- **Stage 2 (across machines and clones), only after stage 1 has been used:**
  one git ref per agent under `refs/agent-log/`, a `sync` that re-runs the gate
  before it pushes and refuses the whole push on a failure, fixed commit
  metadata, advisory claims. A pushed line cannot be recalled, so this stage
  is the first one that cannot be undone, and it may belong in its own repo.
- **Keep it extractable:** no imports from polymorph; the archive path and
  the forbidden words belong in flags or a small config file, not in code.
