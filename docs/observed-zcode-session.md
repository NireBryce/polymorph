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
the harness that needs `CLAUDE.md`, `.claude/skills`, and `.claude/rules` links to what the
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
Hooks pane without starting a review. Neither path produced an error, a toast, or a log line.

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

## The headless CLI and hook trust (probe, 2026-11-05)

A headless probe of the CLI (driven from a live session; report in a scratch dir) found:

- The `zcode` wrapper on PATH boots the desktop app (`--version`, `--help` included). The
  real CLI is the app's own bundle run through Electron-as-node -- the spawn the desktop
  uses for its agents:
  `ELECTRON_RUN_AS_NODE=1 <package>/lib/ZCode/zcode <package>/lib/ZCode/resources/glm/zcode.cjs COMMAND`.
  `version` prints `0.17.1` headless -- the agent runtime's version, a different number from
  the app's 3.17.2. Sessions run via `--prompt` (`--mode` defaults to yolo for `--prompt`
  per the CLI's own help; not behaviorally observed). Other headless subcommands:
  `doctor`, `skills list`, `hooks trust status|review|grant|revoke`, `app-server --stdio`.
- Hook trust works headless through the CLI itself: `hooks trust grant --workspace DIR
  --hook-digest <sha> ...` grants persistent trust, and the runtime's own digests matched
  the hand-computed declaration-digest formula exactly. Hand-writing records (the section
  above) is no longer the only route.
- Unproven: whether workspace hooks fire in a headless session at all. Every `--prompt`
  session died at model authentication before making a tool call -- the account provider
  the desktop uses does not materialize in `--prompt` mode (injected over the protocol),
  and the standalone CLI had no usable provider key. The sessions did read the scratch
  hook wiring.
- Open anomaly: headless sessions logged `config.project_hooks.pending_trust` for hooks
  that every CLI trust surface (`hooks trust status`, `hooks trust review --json`) reported
  `trusted_persistent`. Either a diagnostic from a config-loader path that does not read
  the trust store, or a real gate discrepancy -- one session that reaches a tool call
  settles it.

The trust digest formula, reproduced 26/26 against existing records (kept here because
it took a headless session's runtime to confirm it): the store's digest is
`sha256(JSON.stringify(["workspace-hook-declaration", 1, sourceRelativePath,
sourceDiscoveryOrder, event, matcher, matcherIndex, hookIndex,
["command", command, asyncIsTrue, shellPart], resolvedTimeoutMs, resolvedMaxOutputBytes]))`
with `shellPart` one of `["unset"] | ["true"] | ["string", s]`, `resolvedTimeoutMs =
hook.timeoutMs ?? (hook.timeout ? hook.timeout * 1000 : 60000)` clamped with
`Math.max(1, Math.round(...))`, default `maxOutputBytes` 32768, and an appended
`["onError", "deny"]` entry when the hook sets `onError: "deny"`. `hooks trust status
--json` prints the same digests, and `hooks trust grant --workspace DIR --hook-digest ...`
(`--all-current --bundle-digest <sha>` for the batch form) grants from them headlessly.

Outcome (probe closed 2026-11-06, still one tool call short of the proof): with browser
login done and a provider rule enabled, a headless session selected a model and reached
Z.ai's API -- which answered `429`, code `1305` ("service may be temporarily overloaded")
on both the coding and general endpoints, directly with curl as well as through the CLI.
Auth passes; the model turn does not. Two debugging findings along the way, for whoever
picks this up:

- After a `login`, the shared credential store makes headless provider selection prefer
  the zai account provider, whose project-token resolve fails headless. Pointing
  `ZCODE_DATA_BASE_DIR` at an empty scratch directory gives the run its own credential
  store and restores provider-config selection; the desktop's credentials are untouched.
- The `login`-written provider rule is the coding-plan template (`zhipu-coding-plan-api-key`);
  the same key under the standard template (`zai-standard-api`, plain api-key,
  `https://api.z.ai/api/paas/v4` or the coding endpoint) authenticates but still 1305s.
  Whether plan keys are served to plain API calls at all is a Z.ai plan question, not a
  tooling one.

Proven headless so far: invocation, wiring read by sessions, persistent trust grant,
provider selection, API reach. Still unproven: hooks firing around a completed model
turn -- which is exactly what the zcode capture driver stays blocked on. Polymorph's
zcode tooling has only ever been exercised through stub binaries (the capture and probe
tests); no real zcode session has ever run under it, and the driver's `blocked` flag
comes off only when a live headless session fires one hook.

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
  `toolCallId`/`tool_use_id`), plus `traceId`, `turnId`, `mode`, `permission_mode`, and
  `transcriptPath`/`transcript_path`; `PreToolUse` adds `riskLevel` and `sideEffectScope`;
  `PostToolUse` adds `toolResponse`, `toolResultPreview`, `tool_response`. `tool_input`
  shapes for Bash, Read, Write, and Edit matched Claude Code's (`command` + `description`,
  `file_path` with optional `limit`, `content`, `old_string`/`new_string`/`replace_all`),
  and a `timeout` set on a Bash call passed through.

Derived for the mapping: `zcode.toml` must express deny as exit 2 with stderr as the reason
channel; JSON-answer templates and a PostToolUse deny route have nothing to attach to; and
nothing in this harness can assume fail-closed behavior from a broken hook.

## The other hook events, wiring freeze, and timeouts (3.17.2, second probe round)

Observed on 2026-11-04 in **ZCode 3.17.2**, with a second probe script wired on
`SessionStart`, `UserPromptSubmit`, `PermissionRequest`, and `Stop` (behavior chosen by
`ZCODE_PROBE_MODE` in the hook's command) alongside the first probe:

- Allow + `updatedInput` on `PreToolUse` (a rewritten command handed back to the harness):
  the call ran the original text; the mutation never reached it. **Observed.**
- A hook sleeping 65 s: under the default timeout the call waited out the 60 s kill and then
  proceeded; under an explicit `timeout: 2` it did not wait at all (Pre→Post deltas 60.1 s
  and 0.12 s, from the discovery log's logger write timestamps). Nothing was surfaced either
  time. **Observed.**
- `SessionStart` and `UserPromptSubmit` hooks fire in a session **started after** those
  entries were wired: one `SessionStart` and five `UserPromptSubmit` payloads, all from the
  wired-from-birth session — the session's first input, the pasted probe instructions, and
  the two typed UPS marker prompts among them. An older session, started before the wiring
  existed, produced no such payloads at all. Both carry the tool events' duplicated field
  set; `SessionStart` adds `model` and `source`, `UserPromptSubmit` adds the full `prompt`.
  **Observed.** Derived: hook wiring is read at session start;
  config edits land only in sessions started afterwards. The trust store is the exception
  that is re-read (see the trust-gating section). **Observed + derived.** Two agent
  sessions wiring hooks in one workspace overwrite each other's entries on disk — last
  writer wins, and a frozen session can never see the other's edits — so wire one writer
  at a time and diff the config before trusting a run's setup: on 2026-11-04 the
  round-3 session ran on a fourteen-hook wiring while a parallel session's rewrite landed
  underneath it, and each experiment stayed valid only because its wiring snapshot
  predated the other's write.
- Neither `UserPromptSubmit` action is honored: the prompt carrying the block marker was
  logged and still reached the model (no refusal), and the context-marker prompt arrived
  bare, with no injected stdout line. **Observed.**
- `Stop` fires on every reply end: four payloads in the wired-from-birth session. (An
  earlier read taken before those replies had ended saw zero, which calls this event dead
  when it is not — check reply boundaries, not just the log, before concluding.) Each
  payload carries `stopHookActive`, `responseText`, `responsePreview`,
  `last_assistant_message`, and `toolCallCount`. Its action is not honored: an `exit 2`
  asking for one continuation produced no continuation across all four fires, and the
  second Stop hook's side effect — consuming the marker file armed at `SessionStart` —
  never appeared in any of them, while the first hook logged every dispatch.
  **Observed**; why the second hook never completed is not determinable from outside the
  harness.
- `PermissionRequest`: zero payloads through the typed marker prompts — the probe's ask
  answer drew no dispatch, and the stage read as absent. A later read of the shared log
  corrected that: one dispatch landed from the parallel documentation session, for an
  `AskUserQuestion` call (its `PreToolUse` landed 0.1 s earlier, under `permission_mode`
  `yolo`; the payload adds `requestId` and `reason`). What triggers the stage beyond
  question prompts, and whether a hook answer can act there, is untested. **Observed**;
  a mapping still cannot route a PreToolUse ask through it.
- Matchers are case-sensitive: a hook wired under a lowercase `bash` matcher never fired
  against tool name `Bash` in either session; its append-only log file was never created.
  **Observed.**
- Removing a hook's entry from the config while an older session runs: the call that entry
  used to block proceeded — the frozen wiring still aims at the removed script, which now
  fails to spawn and fails open. New sessions read the clean config. **Observed**;
  reloaded-vs-failed-open is not distinguishable from inside the session, and a positive
  reload test would need a new entry, which is trust-gated (see the trust-gating section).
- `discover summary` counts **tool events** by default, so `SessionStart` /
  `UserPromptSubmit` payloads hide behind the `hidden:` line — the census above came from
  reading the log directly (`--event EVENT` selects them). **Observed.**

Derived for the mapping and the skill: `zcode.toml`'s `session_start = "SessionStart"` is
live-evidenced as a real event name, but nothing exercised it in the session that carried
the mapping; `UserPromptSubmit` exists as a raw event with no canonical slot in the schema.
And `new-mapping`'s live check must start the probing session **after** the hook config is
wired: a session opened before the wiring cannot see it, which reads exactly like a broken
mapping.
