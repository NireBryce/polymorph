# The agent log

How agents working in separate worktrees of one clone see each other's work:
one short, machine-readable line per event, in a shared uncommitted file that
is only ever appended to, with the entries worth keeping archived into the PR
when the branch ships. Skill `agent-log` is the procedure; `agent_log.py` next
to this file is the code; this page is the style guide and the way out.

It is a prototype (`README.md` here lists its known limits and every skill
that references it), repo tooling and not part of polymorph's product (no
mapping, no canon, no `src/`). Nothing here promises compatibility.

## What it is for

- **Not stepping on each other.** A claim says which paths an agent is
  changing; the next agent reads it before editing the same files.
- **Catching up cheaply.** A reader runs `read` and gets open claims plus
  the last 30 entries not yet archived, not a transcript.
- **A record that outlives the worktree.** `git log` already holds what was
  committed. The log holds what git cannot: intent, work in progress, and
  work that was dropped. The archive keeps the part of that worth reading
  later: decisions, gotchas, dropped work, and sops changes.

Not for: per-tool-call tracing, conversation summaries, or anything a commit
message already says.

## The line

```
2026-11-09T14:02Z id=3fa91c0b2e wt=NAME kind=claim paths=src/polymorph/links.py,equip.py | splitting link tables out of equip
2026-11-09T14:20Z id=a07c55d1e9 wt=NAME kind=done sha=ab12cd3 | link tables now in links.py, equip.py imported them twice
2026-11-09T14:31Z id=51be0c7a44 wt=NAME kind=gotcha | flake ignores untracked files so git add came first
2026-11-09T14:40Z id=c2d8e6f013 wt=NAME kind=sops verb=added paths=secrets/prod.yaml
```

`TS id=ID wt=WORKTREE kind=KIND [ref=ID] [verb=VERB] [sha=HEX] [paths=A,B] [| text]`.

- **Time** is UTC to the minute. **`wt`** is the worktree's directory name.
- **`id`** is the first ten hex characters of a hash of the rest of the line,
  set by `add`, printed by `add`, and kept for good. Claims, flags and `read`
  refer to entries by id, never by line number.
- **Text** is printable ASCII, 1-200 characters. The "why" is optional and goes
  in the same sentence. A `sops` entry has no text.

| kind | when | in the archive |
|---|---|---|
| `claim` | starting work on named paths | no |
| `release` | finished with those paths; `--ref ID` closes one claim, none closes all of this worktree's | no |
| `done` | after a commit or a finished piece; `sha=` the commit | no (`git log` has it) |
| `decision` | a choice a later reader would otherwise reverse | yes |
| `gotcha` | something that surprised you | yes |
| `abandon` | the work was dropped; also closes this worktree's open claims | yes |
| `sops` | a change to a sops-managed file: `--verb added\|rotated\|removed\|reencrypted`, `--paths FILE`, no text | yes |

`redacted` is the kind `resolve --redact` leaves in the archive. `add` refuses it.

## What to write: what happened, not what to do

Entries describe. They do not instruct. A later agent reads these lines as
data, and a line shaped like a procedure ("first run X, then edit Y") can be
mistaken for an instruction to it.

| Write | Not |
|---|---|
| `moved link tables into links.py` | `move the link tables into links.py` |
| `flake ignores untracked files so git add came first` | `always git add before nix flake check` |
| `--verb added --paths secrets/prod.yaml` (a sops entry) | `ran sops set to add the entry with the new value` |

Say how only when the method is the point ("rebased instead of merged because
the branch had one commit"). Never paste tool output, error text, file contents
or environment values; write the sentence in your own words. A change to a
sops file is recorded with the `sops` kind and nothing else: which verb, which
file. When a lesson deserves to be a rule, it goes to
`wiki/agents-lessons-learned.md` (skill `record-a-lesson`), not here.

## Commands

```sh
just agent-log add claim --paths src/polymorph/links.py "splitting link tables out of equip"   # prints the entry id
just agent-log add done --sha ab12cd3 "link tables now in links.py"
just agent-log add decision "kept the tables in links.py, not a new module, to leave imports alone"
just agent-log add sops --verb added --paths secrets/prod.yaml
just agent-log add release --ref 3fa91c0b2e "finished"
just agent-log read                         # open claims, then the last 30 entries not yet archived
just agent-log read --paths src/polymorph   # only entries touching a path, or a parent or child of it
just agent-log read --claims                # open claims only
just agent-log read --archive               # include archived entries
just agent-log archive --dry-run            # at ship time; then without --dry-run
just agent-log prune --wt NAME              # delete a gone worktree's live log once its entries are archived
just agent-log-check                        # the rules over everything committed; CI runs it
```

Without `just`: `python3 prototypes/agent-log/agent_log.py ...`. The live log
is `$(git rev-parse --git-common-dir)/agent-log/WORKTREE.log`, shared by every
worktree of the clone and never committed. A claim older than 24 hours is shown
`[stale]`; nothing releases it for you.

## Dropped work

The entries stay. Dropping a branch does not delete its log, and does not need to:

- Write `abandon` (it closes the worktree's open claims). The line stays
  readable in `read`, so the next agent sees the work was dropped and why.
- If the abandoned work is worth keeping on record, archive it with a later
  PR: `just agent-log archive --branch feat/other --wt dropped-worktree`.
  Otherwise it stays local and uncommitted.
- A worktree that vanished without an `abandon` leaves a claim that goes
  `[stale]`. Write the `abandon` for it:
  `just agent-log add abandon --wt NAME "worktree removed, work not shipped"`.

## Archiving at ship

Skill `ship` runs `just agent-log archive` before the commit. It:

1. re-validates every live entry of the kinds below for this worktree with the
   current rules, and checks that each `paths=` item exists in the repo or at
   `HEAD`; one failure stops it with nothing written;
2. copies `decision`, `gotcha`, `abandon` and `sops` entries not already in the
   archive to the end of `wiki/agent-log/BRANCH.log` (slashes become dashes),
   which the PR commits;
3. leaves the live log exactly as it was. `read` hides entries whose id is
   archived. `prune --wt NAME` deletes a live log whose entries are all archived.

The archive is only appended to: a second archive adds lines at the end and
moves nothing. Archived lines are public as soon as the PR is.

## Guards

All of these run in `add` and again in `archive`; `just agent-log-check` (part
of `just preflight`, so CI) re-runs the current rules over everything
committed, so a rule added later reaches old lines. A failing line is rejected,
not scrubbed, and the message names the rule, never the matched text.

- **Grammar.** Fixed fields, `kind` from the table, repo-relative `paths`,
  a real timestamp, printable ASCII, 200 characters, and an id that matches the
  line (an edited archived line fails `check`).
- **Paths exist.** For the kinds that get archived, every `paths=` item must be
  a file or directory in the repo or at `HEAD`, so a value cannot ride in a
  path. Claims may name files not yet created; they are never archived.
- **Secret shapes.** Token and key prefixes, UUIDs, long hex strings, long
  alphanumeric runs, URLs with credentials, `key=value` and `key: value`
  pairs, home directory paths.
- **The machine's own values.** The value of any environment variable whose
  name looks like a key, token, secret, password, credential or auth, and the
  git user name and email.
- **Instruction shapes.** Backticks, `$(` and `${`, and wording such as
  "ignore previous".
- **sops, and anything near it.** sops is the secrets store. A change to one is
  a `sops` entry: a verb and a path, no text, so there is nothing to scan. The
  rules reject sops ciphertext markers (`ENC[`) and age keys anywhere. Any
  other line that mentions sops, a secret, a credential, a password, a token, a
  key, an API, a vault or a key ring is held to a stricter grammar: letters,
  spaces, `.`, `,` and `-` only, 80 characters at most, so no value, identifier,
  host name or path can ride along.

These are heuristics. They lower the chance a secret reaches a committed
file. They do not prove it did not: a short password written as a plain word
passes, and so does anything in a path that really exists. Review of the PR
diff is the check that matters. A rule is only trusted after a test plants the
violation and sees that rule's own message (`tests/test_agent_log.py`).

## When something got through

Lines are committed to a public repository, so a leak is a leak: rotate the
credential first, then record it. Entries are named by id (`read --archive`
shows it).

```sh
just agent-log flag 3fa91c0b2e "a token reached this entry"
```

Describe where and what kind, never the value (the rules apply to the reason
too). That creates `wiki/agent-log/flags/3fa91c0b2e.flag`, and
`just agent-log-check` fails while any flag has no matching `.resolved` file,
so CI stays red until someone deals with it. After rotating:

```sh
just agent-log resolve 3fa91c0b2e "token rotated, entry redacted" --redact
```

`--redact` replaces the entry in the working tree with a `kind=redacted` line
that keeps its id. The old text remains in git history; removing it from
history rewrites a published branch, which is the user's decision, not an
agent's. `ls wiki/agent-log/flags/` is the record of every flag and resolution.

The whole-repo review sweep (skill `review`) lists that directory and runs
`just agent-log check`, which re-runs the current rules over every archived
line.

## Undoing this

Stopping is safe at any point; nothing else depends on the log.

1. Stop writing. Remove skill `agent-log` and its row in `AGENTS.md`, the
   "Log your work" hard rule, and the steps it added to skills
   `use-a-worktree`, `ship`, `review` and `docs-sync` (`git grep agent-log`
   lists every place).
2. Remove `prototypes/agent-log/` (the script, this guide, the README),
   `tests/test_agent_log.py`, the `agent-log` and `agent-log-check` recipes
   (and `agent-log-check` from `preflight`, and its name in
   `tests/test_run_recipe.py`'s preflight assertion), the `wiki/patterns.md`
   row and the `wiki/agents-lessons-learned.md` row that names
   `tests/test_agent_log.py`, then `just docs`.
3. Decide about the archive. `wiki/agent-log/` can be deleted, or kept as a
   record; nothing reads it once the script is gone.
4. The live log is local: `rm -r "$(git rev-parse --git-common-dir)/agent-log"`
   on each clone.

What stays regardless: the archived lines remain in git history and in every
clone and fork that fetched them, and the live log stays on disk until
removed. Undoing does not recall anything. If a leak is the reason for undoing,
rotate the credential first; scrubbing history is a force-push to a published
branch, and that is the user's call.

## Not covered

- Agents on different machines or clones. The live log is in one clone's git
  directory; git moves nothing between clones without a push and a fetch.
  A pushed ref per agent would do it, with the same guards run before the
  push, since a pushed line cannot be recalled. Not built (README, "Where it
  is going").
- Enforcement. A claim is advisory: nothing blocks an edit to a claimed path.
