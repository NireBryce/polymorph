# Agent log, stage 2: coordination across machines and clones

_Status: design, 2026-11-10. Not built. The agent log is a prototype ([README](README.md)); this page says what stage 2 would be, so it can be built outside the conversation that designed it. The tasks are T8 to T10 in [the plan](../../docs/plan-next-phases.md)._

## The problem

The single-clone log lives in one clone's git directory, so only worktrees of that clone see it. Git moves nothing between clones without a push and a fetch. For agents on different machines, the only shared channel today is the archive in a merged PR, which is written once, at ship time, and shows nothing of claims or work in progress.

## Constraints

Standard library only. Entries describe what happened, never what to do. A pushed line cannot be recalled, so the secret rules must run before the push. Reading stays cheap in tokens. Dropped work stays visible. Nothing from outside the repository is ever executed.

## Transports compared

| | Latency | Conflicts | A fresh clone needs | Force-push or deleted ref | Secret scanning sees it | A read needs the network |
|---|---|---|---|---|---|---|
| Per-agent refs `refs/agent-log/<agent>` | a push, then the next fetch | none: one writer per ref, every push is a fast-forward | a refspec `+refs/agent-log/*:refs/agent-log/*` | the ref vanishes; objects stay fetchable by sha on GitHub | unverified for custom refs; assume no | only to refresh |
| One shared orphan branch | same | every agent races on one ref | nothing | one force-push erases everyone's log | probably yes | same |
| git notes | same | one shared notes ref, same race, plus notes-merge | a refspec | same | probably no | same |
| Issue or PR comments through `gh` | about a second per call | none | `gh` and auth | edit and delete exist, but edit history and notification emails keep the text | yes for public repositories, after posting | every read, and the rate limit applies |
| A shared service | lowest | whatever is built | an endpoint and auth | n/a | no | every read |

Per-agent refs are the only option with no write races and no new dependency. Branch protection and rulesets cover branches and tags, not custom refs, so nothing protects these refs from deletion. Notes attach to commits, which is the wrong model for claims. Comments need `gh` on every read and cannot be taken back. A service is out of scope.

## Design

**Refs.** `refs/agent-log/<agentid>`. Each commit is one entry: the commit message is the log line, the tree is empty, and the metadata is fixed (author and committer `agent-log <agent-log@invalid>`, UTC) so no name, email, or timezone leaks. The agent id is a random id stored with `git config --worktree`, never a hostname. No claim refs.

**Line format.** The stage 1 format: `TS id=HEX10 wt=NAME kind=KIND [ref=ID] [verb=V] [sha=HEX] [paths=A,B] [| text]`, with the same kinds and the same rules. Ids de-duplicate across clones and are what flags and releases refer to.

**Commands and when each touches the network.**

| Command | Network | What it does |
|---|---|---|
| `add` | no | validates, then commits to the local ref |
| `sync` | yes | re-runs the rules over every commit between the remote ref and the local one and refuses the whole push if any line fails; pushes the agent's own ref as a fast-forward; fetches every agent's ref |
| `read` | no | reads the local copies; warns when the last fetch is older than N minutes |
| `claim` | yes | `add`, then `sync`, then reports overlapping claims |
| `flag ID REASON` | yes | `add`, then `sync` |

**Claims stay advisory.** A ref can do compare-and-swap (`git push SHA:refs/agent-claims/HASH --force-with-lease=refs/agent-claims/HASH:` succeeds only if the ref does not exist), but a lock costs a network round trip per claim, does not cover overlapping paths (`src/` and `src/a.py` both succeed), and a crashed agent holds it until it expires. Instead: add, push, fetch, then report overlaps. When two agents claim within seconds, both see the overlap after the fetch, and a fixed rule (earlier timestamp, then lower id) says who backs off.

**The gate before a push.**

- The stage 1 rules, unchanged.
- `paths=` on archived kinds must name tracked files.
- The commit metadata must equal the fixed values.
- The first `sync` of a session asks the user to confirm, because everything pushed is public at once.
- A `pre-push` hook refuses `refs/agent-log/*` unless the script is the one pushing.

**What changes from stage 1.** Ids and the `sops` kind matter more, because a pushed line cannot be recalled. Append-only comes free: a ref is a commit chain and the script never force-pushes. One file per flag becomes unnecessary: a single-writer ref has no merge conflicts, so a flag is an entry on the flagger's own ref. `--redact` stops meaning anything: a pushed object outlives any rewrite, so after a leak the only remedies are rotating the credential and a request to GitHub support. The commit author, committer, email, and timezone are a new leak channel, closed by the fixed metadata. The committed wiki archive becomes optional; the refs are the record, though a weak one, since nothing stops a ref from being deleted.

## Build order

Each step is useful or harmless on its own.

1. `sync --dry-run` (T8): create and read the local refs, run the gate, show what would be pushed. Pushes nothing.
2. Push and fetch (T9): the first-sync confirmation, the `pre-push` hook, tests against a local bare remote, one hand-run against a throwaway repository.
3. Claims through `sync` (T10): the overlap report and the back-off rule.

## Not building

Locks or leases. The comments transport, a service, notes, or an orphan branch. `--redact` on refs. Automatic push on every `add`. A recent-flags sweep (listing the flags directory does it).

## Failure modes that could make this not worth building

- **Forks.** Contributors who work from a fork cannot push to upstream refs, so coordination covers only people with write access.
- **Leaks are permanent and public.** A secret that passes the gate is public for good, and GitHub's scanning may not look at custom refs (unverified, T6).
- **Agents push without asking.** Left alone, they would push without a per-push confirmation, which cuts against the repository's one-confirmation rule; the first-sync prompt is the compromise.
- **It may be the wrong problem.** If the agents are one person's sessions on one or two machines, a shared clone plus the single-clone log is cheaper.

## Where it lives

Repo tooling in `scripts/` or `prototypes/`, not part of polymorph's product: it coordinates this repository's own development and has nothing to do with equipping a harness, and as a product feature it would push to users' remotes on their behalf. Whether it becomes its own tool is #111, to be decided before T8.

## Unverified

Everything about GitHub's behaviour above was reasoned from how git and GitHub work, not tested: whether secret scanning or push protection sees `refs/agent-log/*`, whether objects stay fetchable by sha after a ref is deleted, and what the rate limits do to a `sync` loop. T6 tests the first; the others stay listed here until someone tests them.
