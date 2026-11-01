# Observed: a live ZCode session (this repository)

How observed: the author's own session running in **ZCode 3.16.2** with the GLM model,
2026-11-01, in this repository. Passive in-session observation and polymorph's own
read-only commands only: no new harness session was launched, no capture ran, no hook
was wired. Everything below says which of the three it is: **observed** (seen directly
in the session), **derived** (a conclusion from observed facts), or **untested**.

## Session markers

Observed: the session environment carries 13 `ZCODE_*` variables, among them
`ZCODE_APP_VERSION=3.16.2`, `ZCODE_ENV=production`, `ZCODE_PROCESS_LABEL=local-1`,
`ZCODE_GEN_UI_OUTPUT_ROOT` (pointing into `~/.zcode/v2/visualizations`) and
`ZCODE_BASE_URL`. Also `CHROME_DESKTOP=zcode.desktop`. These are the concrete shape of
the "session markers" the env contract's `[harness.NAME] exports` declare: children of
the session inherit them (`--exec 'printenv | grep -c ^ZCODE_'` counted 13 through the
contract).

## Agent sockets

Observed: the session environment sets **no `SSH_AUTH_SOCK`** — an arriving agent
inherits no ssh-agent socket. It does set `SSH_ASKPASS` (ksshaskpass), so an ssh
operation needing a passphrase would prompt through askpass. Derived: a host's env.toml
`[vault] unset` list is still the right control (the tool proved it strips an injected
`SSH_AUTH_SOCK` from a child), because whether a socket exists is the host's choice, not
the harness's.

## Memory

Observed: the session's opening context contains the full text of this repository's
`AGENTS.md` — the harness read it before the first turn. Derived: the zcode coverage
table's `memory = ["AGENTS.md"]` rests on a live session, not only hook-script comments.
Untested: whether the harness also reads `CLAUDE.md` as a separate file — in this
repository it is a symlink to `AGENTS.md`, so the two are indistinguishable from inside.

## Skills through the store

Observed: the session's skill list resolves `.agents/skills/` — itself a symlink into
`.polymorph/basecamp/skills/` — and a skill that was created in the store mid-session
loaded through the chain when invoked. Derived: the zcode coverage table's
`skills = ".agents/skills"` holds through a basecamp-store symlink layout, which is the
deployment shape `adopt` produces.

## The env contract, live

Observed: with a host-facts fixture (not this repository's — env.toml holds machine
facts), `polymorph env --harness zcode` emits the contract as eval-able shell; an
`--exec` child carries `GIT_SSH_COMMAND` and the session markers; the `[vault] unset`
strips an injected `SSH_AUTH_SOCK` from the child; and the emit form `eval`s cleanly.

## A design finding: pointers are not gaps

Observed on three repositories (this one, factcheck, nixos-configs): all three use the
same pointer pattern — `CLAUDE.md` is a symlink to `AGENTS.md`, and `.claude/skills` a
symlink to `.agents/skills`. The gap report counts each provided *path* the harness does
not honour, so zcode is reported as ignoring `CLAUDE.md` while honouring the very bytes
it points at via `AGENTS.md`. Derived: the gap report should deduplicate by resolved
content — a symlink to honoured content is a pointer, not a gap. Not yet implemented;
tracked on the tracker.
