# Observed: OpenCode v2.0.32 (2026-10-30, live server, discovery plugin)

OpenCode has no `settings.json` command hooks. Its V2 plugins register tool hooks in
JavaScript, so discovery used a small plugin that pipes each payload to
`polymorph discover log` ([example plugin](examples/opencode-discovery-plugin.js)). The
`claude` mapping does not apply; a polymorph mapping for it would need a generated plugin,
which polymorph does not produce.

Sources, kept apart because they differ in strength:

- **Live**: a running v2.0.32 server with the plugin loaded, about 4,100 logged payloads
  (the log kept growing, because the probes were themselves logged tool calls).
- **Documented**: the V2 plugin docs (opencode.ai/v2/docs) read alongside the live runs.
- **Seen in the binary, untested**: strings and code read out of the installed binary. These
  were not exercised, so a build with different code behaves differently.

## Plugin location and export shape

- Live: a plugin in `.opencode/plugins/NAME/index.js` under the project was loaded as soon as
  the file appeared, with no restart. `opencode plugin list` showed it as `local`.
- Each immediate child **directory** of `.opencode/plugins/` is one plugin. Bare `.js` files
  directly under `plugins/` are not loaded, and the singular `plugin/` directory is not
  read (binary, untested for the directory forms; live for the working path).
- Entry file: `server`, else `index`, with any standard extension (binary).
- Default export is an object with `id` and either `setup(ctx)` or `effect` (binary and
  docs). `setup` may be async and may return a cleanup function. A V1-style `server()` hook
  map is ignored by this build.
- Other places (docs and binary, not used): a `plugins` array in `opencode.json`, a global
  config directory, and packages with `package.json` `exports`.

## Hooks and payload fields

- Tool hooks are registered with `ctx.tool.hook("execute.before", fn)` and
  `ctx.tool.hook("execute.after", fn)`. The event names a logger sees are therefore
  `execute.before` and `execute.after`, not Claude-style names.
- Native `execute.before` fields (documented, and `agent` seen live): `tool`, `sessionID`,
  `agent`, `messageID`, `id`, `input`. `execute.after` adds `status` (`completed` or
  `error`) and `result` (`output`, `content`, `metadata`) or `error`.
- Live shape of what the example plugin logged (it renames `tool` and `sessionID` to
  `tool_name` and `session_id`): `hook_event_name`, `tool_name`, `session_id`, `agent`,
  `input`, and on the after line `output`. The capture did not include `id` and
  `messageID`, so pairing before and after lines by id was not exercised live.
- Both hooks fired for every step tried: shell, write, read, edit, grep, and a background
  shell command.
- `input` keys seen: shell `command`, `timeout`, `workdir`, `background`; edit `path`,
  `oldString`, `newString`; write `path`, `content`; read `path`, `offset`, `limit`; grep
  `pattern`, `path`. File paths are `path`, not `file_path`.
- There is no session-start hook. The closest is the `session.created` event from
  `ctx.event.subscribe()`, whose envelope is `{ id, created, type, data: { sessionID, info } }`
  (binary).

## Tool names and background commands

- Live: `shell`, `edit`, `write`, `read`, `grep`. Binary catalog also lists `patch`, `glob`,
  `skill`, `subagent`, `webfetch`, `websearch`. The shell tool is `shell`, not `bash`.
- There is no separate background-command tool. Background is a `background` boolean in the
  `shell` input (and in `subagent`). Live: the call fired both hooks at submission; the
  command's completion produced no hook line.

## Hot reload

Live: editing the plugin file reloaded it within about a second (a "loading plugin" line in
the server log), and the next tool call carried a field added by the edit. No server or
session restart. A hook-added field does not reach calls already in flight.

## Blocking a tool call

None of this was tested; polymorph's `exit_2_blocks`, `ask` and `rewrites_input` flags
stay `false` for OpenCode.

- **Documented**: the permission layer. `ctx.permission.hook("evaluate", fn)` receives the
  action and resources and blocks by setting `event.effect = "deny"` (or asks with
  `"ask"`). A configured `deny` rule is final and skips the hook.
- **Documented**: `event.input` is mutable in `execute.before`, and `event.result` in
  `execute.after`. The tool hooks have no block flag or return value.
- **Seen in the binary, untested**: a throw from an `execute.before` callback aborts the tool
  call before it runs. The docs do not describe this as a blocking API.

## Noise: `session.created` and the server-wide bus

Hooks and the event bus belong to the server, not to one session or project. The capture
mixed activity from every session on the server, and one file held lines from unrelated
projects.

- About 5,400 of about 5,500 log lines were `session.created` events from internal child
  sessions (titles, compaction, subagents) and shell sessions, against about 70 tool
  events. Tool fields therefore appeared on 67 of 4,095 payloads.
- Some `session.created` lines had an empty `session_id` from an earlier version of the
  plugin that read the wrong field.
- Each event is logged by its own process, so lines land out of order: a background call's
  `after` line had an earlier `ts` than its `before` line by a few milliseconds.

`discover summary` and `draft` now default to tool events, sort by `ts` and pair by call id
and message id for this reason ([CLI reference](cli.md#discover-summary)).

## What the first draft got wrong

The draft run on this capture (before those changes) grouped every event under `<invalid>`
(dots were rejected in event names), left `[events]` empty, left `[payload_out]` empty
(output values were reduced to a bare type and length) and put `grep` under `read`. It
also guessed `shell`, `edit` and `read` classes from the names, which matched.

## Not tested

- Blocking by any route above, `ask` prompts, input rewriting, and what the model sees
  when a hook throws.
- Pairing by `id` and `messageID` on a live run.
- MCP tools, `subagent`, `webfetch` and `websearch` hooks (only the five tools above ran).
- Other OpenCode versions, other platforms, and a restart of the server while a session
  is running.
- Whether a hook fires for a tool call made inside a subagent session (the bus is global,
  but this was not isolated).
