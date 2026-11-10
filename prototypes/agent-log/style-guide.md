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
file. If the reason matters, add a separate `decision` or `gotcha` in the
strict form (letters only, no value, host or path). When a lesson deserves to be a rule, it goes to
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

## Fixing a mistake

Lines are never edited: an edited archived line fails `check` because its id no
longer matches. Correct a wrong line with a new line that says what is true
now. A live line that must go is removed by the user by hand (the log is a
plain file); an archived one goes through `flag` and `resolve --redact`.

## Options and exit codes

Before the subcommand: `--log-dir DIR` (the live log; default the git common
dir's `agent-log`), `--archive-dir DIR` (default `wiki/agent-log` in the repo
root) and `--repo-root DIR` (where archived `paths=` must exist; default the git
top level). Outside a git repository, pass all three and `--wt`. `read` also
takes `--tail N` (default 30) and `--stale-hours N` (default 24). `archive`
defaults `--wt` to this worktree's directory name and `--branch` to the current
branch (on a detached HEAD pass `--branch`).

Exit codes: 0 done; 1 `archive`, `prune` or `check` found a problem (and wrote
or removed nothing); 2 usage error, or an entry the rules reject.

## Dropped work

The entries stay. Dropping a branch does not delete its log, and does not need to:

- Write `abandon` (it closes the worktree's open claims, even after the entry
  is archived). The line stays readable in `read` until it is archived, and
  afterwards in `read --archive` and the archive file, so the next agent sees
  the work was dropped and why.
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
   archived in this checkout. `prune --wt NAME` deletes a whole live log, and
   refuses unless every archivable entry in it is archived.

The archive is only appended to: a second archive adds lines at the end and
moves nothing. Archived lines are public as soon as the PR is.

Things to know:

- The archive is the `wiki/agent-log/` of the checkout you run it in. Another
  worktree on another branch does not have your archive file until it merges, so
  its `read` still shows those entries. Archive and `read` dedupe against this
  checkout only.
- Two entries with the same text, kind and fields in the same minute have the
  same id and are archived once.
- `prune` deletes the whole file, including `claim`, `release` and `done` lines
  that were never archived, and the archive it relies on is uncommitted until
  the PR is. Prune only a worktree that is gone, after its PR has merged.
- A claim whose closing `release` or `abandon` has been archived is hidden by
  `read` along with it, so a dropped claim does not read as work without an end.
  `read --archive` shows both.
- `read` skips live lines that do not parse or carry an impossible timestamp;
  `prune` and `archive` stop on them. A claim with no `paths=` is shown in
  `read` but never in `read --paths`.
- A live line that `archive` or `prune` refuses (hand-edited, or caught by a
  rule added later): delete it from your worktree's live file and add a
  corrected one. It is a plain local file; the id changes if a line is edited.

## Guards

All of these run in `add` and again in `archive`; `just agent-log-check` (part
of `just preflight`, so CI) re-runs them over everything committed, so a rule
added later reaches old lines. The one exception is "Paths exist", which is
checked at `add` and `archive` only. A failing line is rejected,
not scrubbed, and the message names the rule, never the matched text.

- **Grammar.** Fixed fields, `kind` from the table, repo-relative `paths`,
  a real timestamp, printable ASCII, 200 characters, and an id that matches the
  line (an edited archived line fails `check`).
- **Paths exist.** For the kinds that get archived, every `paths=` item must be
  a file or directory in the repo or at `HEAD`, so a value cannot ride in a
  path. Claims may name files not yet created; they are never archived. This is
  checked when an entry is added and when it is archived. `check` does not
  repeat it, so a later rename or delete does not break an old archived line.
- **Secret shapes.** Token and key prefixes, UUIDs, hex strings of 16 or more
  characters, runs of 24 or more letters and digits (a run containing `/`
  passes as a path unless one piece of 8 or more characters mixes digits with
  letters, or upper with lower case), URLs with credentials, `key=value` and `key: value`
  pairs, home directory paths. `sha=` is accepted only on `done` entries,
  which are never archived, because a hex token fits in it.
- **The machine's own values.** The value of any environment variable whose
  name contains key, token, secret, passw, cred, auth or sops (six characters
  or more), and the git user name and email (four characters or more, matched
  as whole words, so write 'the user').
- **Instruction shapes.** Backticks, `$(` and `${`, and a few phrases such as
  "ignore previous". That is all: "say what happened, not what to do" is a
  convention this page asks for, not a rule the script enforces, and a line
  that reads as a command passes.
- **sops, and anything near it.** sops is the secrets store. A change to one is
  a `sops` entry: a verb and a path, no text, so there is nothing to scan. The
  rules reject sops ciphertext markers (`ENC[`) and age keys anywhere. Any
  other line that mentions sops, a secret, a credential, a password, a
  passcode, a passphrase, `pw`, a token, a vault, a key ring, pgp or gpg is
  held to a stricter grammar: letters,
  spaces, `.`, `,` and `-` only, 80 characters at most, so no value, identifier,
  host name or path can ride along.

The `wt=` field is whatever `--wt` says (letters, digits, `.`, `_`, `-`, 60
characters), so a short value can ride there; the default is the worktree's
directory name.

These are heuristics. They lower the chance a secret reaches a committed
file. They do not prove it did not: a short password written as a plain word
passes, and so does anything in a path that really exists. Review of the PR
diff is the check that matters. A rule is only trusted after a test plants the
violation and sees that rule's own message (`tests/test_agent_log.py`).

## When something got through

Lines are committed to a public repository, so a leak is a leak. In order:
tell the user; record it with `flag` (CI goes red on purpose); the user
rotates the credential; then `resolve`. Entries are named by id
(`read --archive` shows it).

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
that keeps its id; use it for a leaked value, and leave it off only if the line
should stay readable. The flag and `.resolved` files are committed with the PR
that fixes the leak (skill `ship`). The old text remains in git history; removing it from
history rewrites a published branch, which is the user's decision, not an
agent's. `ls wiki/agent-log/flags/` is the record of every flag and resolution.

The whole-repo review sweep (skill `review`) lists that directory and runs
`just agent-log check`, which re-runs the current rules over every archived
line.

## Undoing this

Stopping is safe at any point; nothing else depends on the log.

1. Stop writing. Remove skill `agent-log`, and every place the README's
   "Skills and files that reference it" table lists: the "Log your work" hard
   rule, the commands line and the architecture and skills rows in `AGENTS.md`,
   and the steps added to skills `use-a-worktree`, `ship`, `review` and
   `docs-sync` (`git grep agent-log` lists every place).
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
