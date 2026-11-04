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
content — a symlink to honoured content is a pointer, not a gap. Implemented: a provided path
is reached when a read path resolves to it (see [`inventory`](cli.md#inventory)).

## Which harness needs the links

Read from the harnesses' own documentation (2026-11-02), not observed in a session. Claude
Code loads project skills from `.claude/skills`, rules from `.claude/rules` and instructions
from `CLAUDE.md`; it loads nothing under `.agents/`, and reads `AGENTS.md` itself only from
v2.1.277 and only when no `CLAUDE.md` exists in or above the working directory. ZCode's
repository documents `.agents/skills` and `.zcode/skills` as its skill roots, `AGENTS.md` as
its instruction file, and `.claude/skills` as not part of discovery. Derived: Claude Code is
the harness that needs `CLAUDE.md`, `.claude/skills` and `.claude/rules` links to what the
repo keeps under `AGENTS.md` and `.agents/`; ZCode needs none. A third-party review that has
ZCode reading `.claude/skills` disagrees with ZCode's own documentation. This page's session
observation of `.agents/skills` agrees with the documentation.

## Hook trust gating (3.17.2)

Observed on 2026-11-04 in **ZCode 3.17.2** in this repository, while wiring the discovery
logger per [`discovery`](discovery.md): hooks in `.zcode/config.json` with `enabled: true` on
the root and on every hook never executed — no payload was logged, a deny-answer probe did not
block, an ask-answer probe did not prompt, the log file was never created. Observed in the
harness: each pending hook's enable toggle in Settings → Hooks forced off and disabled, a
per-hook Trust button disabled with the tooltip "This connection cannot trust this Hook.",
and a chat-side "Workspace hooks pending review" popover whose Review button navigated to the
Hooks pane without starting a review. Neither path produced an error, a toast or a log line.

Derived: ZCode gates workspace hooks per workspace behind a trust store
(`~/.zcode/security/workspace-hook-trust-v1.json` on this machine), keyed by a sha256 digest
over each hook's declaration (event, matcher, indexes, resolved command and defaults); the
formula was reproduced exactly against another workspace's existing records. Observed after
the records were written by hand into that store: the gated session's hooks started firing
about 45 minutes later without a restart, so the gate re-reads trust instead of freezing it
at session start. Untested: whether the Trust and Review paths work in another build or
window arrangement. Derived: a wired hook that never fires in ZCode may be pending trust,
not a broken mapping — confirm a hook actually fires before debugging the mapping
(issue 55).

## Hook answers and failure behavior (3.17.2)

Observed on 2026-11-04 in **ZCode 3.17.2**, with a probe hook wired on `PreToolUse` and
`PostToolUse` (matcher `*`) that branched on marker strings in the payload and answered in
one of several ways:

- `exit 2` on `PreToolUse` blocked the call — Bash and Edit alike — and the hook's stderr
  line was the entire visible tool result, verbatim, with no harness wrapper.
- A stdout JSON answer (`hookSpecificOutput` with `permissionDecision` `"deny"`, then
  `"ask"`) did nothing: the call ran, no denial text and no prompt reached the model (the
  ask call's Pre and Post landed 1 second apart). Derived: for these shapes only exit codes
  act; JSON decisions are ignored.
- `exit 1` ran the call, and the hook's stderr never reached the model. A hook sleeping past
  the 60s default timeout was killed and the call proceeded; the harness surfaced nothing
  about either. Derived: ZCode fails open on hook error and on hook timeout — a broken or
  slow guard hook blocks nothing here.
- `exit 2` on `PostToolUse` produced no feedback the model could see.
- The probe's matcher matched marker text inside a later Edit's payload and denied the call
  that was editing the probe itself: matchers run on raw payload text, so a hook script
  quoting its own marker denies its installer. Worked around by writing placeholders and
  materializing the markers with `sed` built from parts.
- Payloads carry every field twice, camelCase and snake_case (`toolName`/`tool_name`,
  `toolCallId`/`tool_use_id`), plus `traceId`, `turnId`, `mode`, `permission_mode` and
  `transcriptPath`/`transcript_path`; `PreToolUse` adds `riskLevel` and `sideEffectScope`;
  `PostToolUse` adds `toolResponse`, `toolResultPreview`, `tool_response`. `tool_input`
  shapes for Bash, Read, Write and Edit matched Claude Code's (`command` + `description`,
  `file_path` with optional `limit`, `content`, `old_string`/`new_string`/`replace_all`),
  and a `timeout` set on a Bash call passed through.

Derived for the mapping: `zcode.toml` must express deny as exit 2 with stderr as the reason
channel; JSON-answer templates and a PostToolUse deny route have nothing to attach to; and
nothing in this harness can assume fail-closed behavior from a broken hook.
