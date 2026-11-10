# The agent log

How agents working in separate worktrees of one clone see each other's work:
one short, machine-readable line per event, in a shared uncommitted file,
archived into the PR when the branch ships. Skill `agent-log` is the
procedure; `scripts/agent_log.py` is the code; this page is the style guide
and the way out.

It is repo tooling, not part of polymorph's product (no mapping, no canon,
no `src/`). Nothing here promises compatibility.

## What it is for

- **Not stepping on each other.** A claim says which paths an agent is
  changing; the next agent reads it before editing the same files.
- **Catching up cheaply.** A reader runs `read` and gets open claims plus
  the last 30 entries, not a transcript.
- **A record that outlives the worktree.** `git log` already holds what
  was committed. The log holds what git cannot: intent, work in progress,
  decisions, surprises, and work that was dropped.

Not for: per-tool-call tracing, conversation summaries, or anything a commit
message already says.

## The line

```
2026-11-09T14:02Z wt=NAME kind=claim paths=src/polymorph/links.py,equip.py | splitting link tables out of equip
2026-11-09T14:20Z wt=NAME kind=done sha=ab12cd3 | link tables now in links.py, equip.py imported them twice
2026-11-09T14:31Z wt=NAME kind=gotcha | flake ignores untracked files so git add came first
```

`TS wt=WORKTREE kind=KIND [sha=HEX] [paths=A,B] | text`. Time is UTC to the
minute. `wt` is the worktree's directory name. Text is printable ASCII,
1-200 characters, no `|`. The "why" is optional and goes in the same
sentence.

| kind | when | survives archiving |
|---|---|---|
| `claim` | starting work on named paths | no (compacted away) |
| `release` | finished with those paths | no |
| `done` | after a commit or a finished piece; `sha=` the commit | yes |
| `decision` | a choice a later reader would otherwise reverse | yes |
| `gotcha` | something that surprised you | yes |
| `abandon` | the work was dropped; also closes your open claims | yes |

## What to write: what happened, not what to do

Entries describe. They do not instruct. A later agent reads these lines as
data, and a line shaped like a procedure ("first run X, then edit Y") can be
mistaken for an instruction to it.

| Write | Not |
|---|---|
| `adding entry to sops` | `run sops set to add the entry with the new value` |
| `moved link tables into links.py` | `move the link tables into links.py` |
| `flake ignores untracked files so git add came first` | `always git add before nix flake check` |

Say how only when the method is the point ("rebased instead of merged
because the branch had one commit"). Never paste tool output, error text,
file contents or environment values; write the sentence in your own words.
When a lesson deserves to be a rule, it goes to
`wiki/agents-lessons-learned.md` (skill `record-a-lesson`), not here.

## Commands

```sh
just agent-log add claim --paths src/polymorph/links.py "splitting link tables out of equip"
just agent-log add done --sha ab12cd3 "link tables now in links.py"
just agent-log read                       # open claims, then the last 30 entries
just agent-log read --paths src/polymorph # only entries touching a path or its parent or child
just agent-log read --claims              # open claims only
just agent-log archive                    # at ship time; --dry-run first if unsure
just agent-log-check                      # the rules over everything committed; CI runs it
```

Without `just`: `python3 scripts/agent_log.py ...`. The live log is
`$(git rev-parse --git-common-dir)/agent-log/WORKTREE.log`, shared by every
worktree of the clone and never committed. A claim older than 24 hours is
shown `[stale]`; the script does not release it for you.

## Dropped work

The entries stay. Dropping a branch does not delete its log, and does not
need to:

- Write `abandon` (it closes the worktree's open claims). The line stays
  readable in `read`, so the next agent sees the work was dropped and why.
- If the abandoned work is worth keeping on record, archive it with a later
  PR: `just agent-log archive --branch feat/other --wt dropped-worktree`.
  Otherwise it stays local and uncommitted.
- A worktree that vanished without an `abandon` leaves a stale claim.
  Write the `abandon` for it: `just agent-log add abandon --wt NAME "worktree removed, work not shipped"`.

## Archiving at ship

Skill `ship` runs `just agent-log archive` before the commit. It:

1. re-validates every live entry for this worktree with the current rules;
   one failure stops it with nothing written and nothing removed;
2. drops `claim` and `release` lines (they are about the moment, not the
   record);
3. writes the rest, sorted and de-duplicated, to
   `wiki/agent-log/BRANCH.log` (slashes become dashes), which the PR commits;
4. removes the archived lines from the live log.

One archive file per branch. Archived lines are public as soon as the PR is.

## Guards

All of these run in `add` and again in `archive`; `just agent-log-check`
(part of `just preflight`, so CI) re-runs the current rules over everything
committed, so a rule added later reaches old lines. A failing line is
rejected, not scrubbed, and the message names the rule, never the matched
text.

- **Grammar.** Fixed fields, `kind` from the table, repo-relative `paths`,
  printable ASCII, 200 characters.
- **Secret shapes.** Token and key prefixes, long hex strings, long
  mixed-case alphanumeric runs, URLs with credentials, `key=value` and
  `key: value` pairs, home directory paths.
- **The machine's own values.** The value of any environment variable whose
  name looks like a key, token, secret, password, credential or auth, and
  the git user name and email and the login.
- **Instruction shapes.** Shell and code characters, a line opening with an
  imperative, wording such as "ignore previous".
- **sops, and anything near it.** sops is the secrets store. The rules
  reject sops ciphertext markers (`ENC[`), its metadata block and age keys
  outright. Any line that mentions sops, a secret, a credential, a
  password, a token, a vault or a key ring is held to a stricter grammar:
  letters, spaces, `.`, `,` and `-` only, 80 characters at most, so no
  value, identifier, host name or path can ride along.

These are heuristics. They lower the chance a secret reaches a committed
file. They do not prove it did not. A rule is only trusted after a test
plants the violation and sees it fail (`tests/test_agent_log.py`).

## When something got through

Lines are committed to a public repository, so a leak is a leak: rotate the
credential first, then record it.

```sh
just agent-log flag BRANCH-SLUG 12 "a token reached this line"
```

Describe where and what kind, never the value (the rules apply to the reason
too). The flag goes in `wiki/agent-log/flags.log`, and `just agent-log-check`
fails while any flag is unresolved, so CI stays red until someone deals with
it. After rotating:

```sh
just agent-log resolve BRANCH-SLUG 12 "token rotated, line redacted" --redact
```

`--redact` overwrites the line's text in the working tree. The old text
remains in git history; removing it from history rewrites a published
branch, which is the user's decision, not an agent's. Flags and resolutions
stay in `flags.log` as the record.

The whole-repo review sweep (skill `review`) runs
`just agent-log check --recent-days 30`, which lists recent flags even when
resolved, and re-runs the current rules over every archived line.

## Undoing this

Stopping is safe at any point; nothing else depends on the log.

1. Stop writing. Remove skill `agent-log` and its row in `AGENTS.md`, the
   "Log your work" hard rule, and the steps it added to skills
   `use-a-worktree`, `ship`, `review` and `docs-sync` (`git grep agent-log`
   lists every place).
2. Remove `scripts/agent_log.py`, `tests/test_agent_log.py`, the
   `agent-log` and `agent-log-check` recipes (and `agent-log-check` from
   `preflight`), and this page, then `just docs`.
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
  push, since a pushed line cannot be recalled. Not built.
- Enforcement. A claim is advisory: nothing blocks an edit to a claimed
  path.
