# Behavior: OpenCode

What OpenCode reads and how its hooks behave, gathered for whoever writes polymorph's OpenCode modules (a coverage table, a mapping, plugin generation). It is a reference, not a plan: nothing here is implemented.

Every fact carries its source, because the sources disagree:

- **v1 docs**: OpenCode's public docs, read from the source repo (`sst/opencode`, `packages/web/src/content/docs/`, commit `2fdc064`, 2026-11-06). The pages are `plugins.mdx`, `rules.mdx` (the "instructions" page), `skills.mdx`, `config.mdx`, `permissions.mdx`, `tools.mdx`, and `cli.mdx`. The skills page calls itself "OpenCode 1".
- **v2 source**: the same commit's `packages/plugin/src` (the V2 plugin API types and README) and `specs/v2/` (design notes; `claude-agent-provider.md` is marked **Draft**).
- **observed**: [observed-opencode.md](../docs/observed-opencode.md), a live v2.0.32 server with a discovery plugin (2026-10-30). This is the only source here that ran.

None of this has been tested against a polymorph-generated config.

## Per-harness, not per-model

What gets read (instruction files, skills, hooks, permissions) is decided by OpenCode, not by the model it talks to. A Claude model inside OpenCode does not read `.agents/settings.json`, `.claude/settings.json` hooks or anything else the way Claude Code would.

The clearest case is the planned **Claude Agent provider** (v2 source, draft spec), which runs the user's own `claude` through the Claude Agent SDK. OpenCode turns off what Claude Code would load for itself: "Settings files in `~/.claude` and the project's `.claude` folder, CLAUDE.md files, hooks, MCP servers and skills". Claude Code's built-in tools are off too, and Claude gets OpenCode's tools instead. OpenCode passes "AGENTS.md files, skills, MCP instructions, project references" itself.

So in OpenCode, whatever the model:

- `.agents/skills` is read **natively**, without a symlink, because OpenCode itself searches that path (v1 docs).
- a Claude Code hook in `.claude/settings.json` or `.agents/settings.json` **never fires**, since OpenCode has no settings-file hooks. A guard has to be an OpenCode plugin.

## Two plugin APIs

The v1 docs and the v2 build that was observed describe different plugin APIs. A module must target one of them and say which.

| | v1 docs (`plugins.mdx`) | v2 (source + observed v2.0.32) |
|---|---|---|
| Module shape | named export of an async function `({ project, client, $, directory, worktree }) => hooks` | `export default { id, setup(ctx) }`, or `Plugin.define({...})` from `@opencode/plugin`; `setup` may return a cleanup function |
| Hook registration | keys of the returned object: `"tool.execute.before": async (input, output) => {}` | `await ctx.tool.hook("execute.before", (event) => {})`, which returns a registration with `dispose()` |
| Location | `.opencode/plugins/` (project) and `~/.config/opencode/plugins/` (global); JS or TS files | observed: each immediate child **directory** of `.opencode/plugins/` is one plugin (entry `server` or `index`); bare files directly under `plugins/` were not loaded |
| npm plugins | `"plugin": ["pkg", "@org/pkg"]` in `opencode.json`, installed with Bun at startup into `~/.cache/opencode/node_modules/` | observed in the binary: a `plugins` array in config (untested) |
| Load time | "automatically loaded at startup" | observed: hot-reloaded about a second after the file changed, with no restart; calls already in flight keep the old hooks |
| Load order | global config, project config, global plugin dir, project plugin dir; "all hooks run in sequence" | not stated |
| Dependencies | `.opencode/package.json`; OpenCode runs `bun install` at startup | not stated |

The v1 directory rule and the v2 observation conflict: v1 says to put files in `plugins/`, while v2.0.32 loaded only directories. A generated plugin written as `plugins/NAME/index.js` matches what v2 loaded. Whether v1 loads that layout is not stated.

The v1 plugin context includes `$`, Bun's shell API. polymorph's plugin must never build a shell string from payload content (AGENTS.md, "Mapping content is data"). The shipped discovery plugin spawns `polymorph discover log` with an argument vector and pipes the payload on stdin; a generated guard plugin should do the same.

## Hooks, mapped to canonical events

| Canonical event | v1 | v2 |
|---|---|---|
| `pre_tool` | `tool.execute.before(input, output)`: `input.tool` is the tool name, `output.args` its arguments | `execute.before` event: `tool`, `sessionID`, `agent`, `messageID`, `id` (call id), `input` |
| `post_tool` | `tool.execute.after` (shape not stated) | `execute.after` event: the same ids, plus `status` (`completed` or `error`) and then `result` or `error` |
| `session_start` | the `session.created` event, through the `event` hook (`event.type`) | no hook; `session.created` arrives on `ctx.event.subscribe()`, as the envelope `{ id, created, type, data: { sessionID, info } }` (observed) |

Other events in v1 docs that a module might want: `permission.asked`, `permission.replied`, `session.idle`, `session.error`, `shell.env` (sets env vars for every shell, AI and user), and `experimental.session.compacting`. In v2 there are also `session.hook("context")` (edit tools and descriptions before each provider call) and `session.hook("experimental.idle")`.

The hooks and the event bus belong to the **server**, not to one session (observed). The observed log mixed lines from every session and project on that server, and `session.created` fired far more often than tool events, because of internal child sessions for titles, compaction, and subagents. Generated hooks must filter by working directory or project, or a guard written for one repo runs against another.

## Blocking, asking, rewriting

These decide the mapping's `[capabilities]`. None has been seen live, so every flag stays `false` until a probe round observes it (skill `new-mapping`; the probe pattern in [patterns.md](patterns.md)).

- **Block, v1**: throw from `tool.execute.before`. The docs' `.env` example does `throw new Error("Do not read .env files")`. What the model sees afterwards is not stated.
- **Block, v2**: the tool-hook types have no block field. Observed in the binary but untested: a throw from `execute.before` aborts the call. The documented v2 route is the permission layer: `ctx.permission.hook("evaluate", fn)` receives `{ sessionID, agent?, action, resources, metadata?, source? }` and may set `effect` (`allow`, `ask`, or `deny`) and `message`. A configured `deny` rule is final and skips the hook (the v2 docs, as recorded in [observed-opencode.md](../docs/observed-opencode.md); not tested).
- **Ask**: v1 has `"ask"` as a permission value and `permission.asked` / `permission.replied` events. v2 has `effect = "ask"` in `evaluate`. Whether a tool hook can raise an ask is not stated in either.
- **Rewrite input**: v1 mutates `output.args` (the docs' example escapes `output.args.command`). In v2, `input` and `tool` are mutable in `execute.before`, while the ids are readonly.
- **Rewrite output**: v2 `execute.after` may change `result`; the changed content is normalized and bounded again (`specs/v2/tools.md`).

Under `opencode run --auto`, permissions that would ask are approved and explicit `deny` rules still hold (v1 docs). A guard expressed only as `ask` therefore becomes an allow under `--auto`. That is polymorph's `ask = false` case: the hook must harden ask to deny itself (SPEC, "Wire protocol").

## Tool names and arguments

Tool names differ between versions, so the mapping's `[tools]` table and `payload_in` paths depend on which version the module targets.

| Class | v1 docs | v2.0.32 observed |
|---|---|---|
| shell | `bash`; arg `command` | `shell`; `command`, `timeout`, `workdir`, `background` |
| edit | `edit`, `write`, `apply_patch` (all under the `edit` permission); arg `filePath` | `edit` (`path`, `oldString`, `newString`), `write` (`path`, `content`); `patch` seen in the binary |
| read | `read` (`filePath`), `grep`, `glob` | `read` (`path`, `offset`, `limit`), `grep` (`pattern`, `path`); `glob` seen in the binary |
| other | `skill`, `todowrite`, `webfetch`, `websearch`, `question`, `lsp`; `task` launches subagents | `skill`, `subagent`, `webfetch`, `websearch` seen in the binary |

- A v1 `apply_patch` call carries `output.args.patchText`, with paths in marker lines (`*** Add File:`, `*** Update File:`, `*** Move to:`, `*** Delete File:`) relative to the project root. polymorph's `apply_patch_paths` extractor reads `input`, `patch`, `command`, and `cmd`, not `patchText`. Supporting it is an extractor change (skill `change-the-schema`).
- v2 has no separate background-command tool; `background` is a boolean on `shell` (and `subagent`). Observed: a background call fires both hooks at submission, and its completion fires none.
- A plugin tool with a built-in's name replaces the built-in (v1 docs). A generated plugin must not register tools.

## Instructions (AGENTS.md, CLAUDE.md)

From v1 docs (`rules.mdx`):

- Project: `AGENTS.md`, found by walking up from the current directory. A `CLAUDE.md` there is used only if no `AGENTS.md` exists, so when both exist only `AGENTS.md` is read.
- Global: `~/.config/opencode/AGENTS.md`, else `~/.claude/CLAUDE.md`.
- Extra files: `"instructions": [...]` in `opencode.json` takes paths, globs (`packages/*/AGENTS.md`) and URLs (5 s timeout), combined with the `AGENTS.md` files.
- `@file` references inside `AGENTS.md` are not expanded.
- `OPENCODE_DISABLE_CLAUDE_CODE_PROMPT=1` disables `~/.claude/CLAUDE.md`, and `OPENCODE_DISABLE_PROJECT_CONFIG` disables project config "and project instruction files" (`cli.mdx`).

For polymorph: a repo whose memory lives in `AGENTS.md` (this repo's basecamp layout) needs no link for OpenCode. A coverage table would record `memory = ["AGENTS.md"]`, and a separate `CLAUDE.md` would not be read.

## Skills

From v1 docs (`skills.mdx`):

- Paths: `.opencode/skills/<name>/SKILL.md`, `.claude/skills/<name>/SKILL.md`, and `.agents/skills/<name>/SKILL.md` in the project, plus the same three under `~/.config/opencode/`, `~/.claude/`, and `~/.agents/`. Project paths are searched walking up from the current directory to the git worktree root.
- Frontmatter: `name` and `description` are required, and unknown fields are ignored. `name` must match `^[a-z0-9]+(-[a-z0-9]+)*$`, be 1–64 characters, and equal the directory name. `description` is 1–1024 characters.
- The model sees names and descriptions in the `skill` tool's description, and loads a skill with `skill({ name })`.
- Permissions: `permission.skill` with patterns (`"internal-*": "deny"`); a denied skill is hidden.
- `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` disables `.claude/skills`, and `OPENCODE_DISABLE_EXTERNAL_SKILLS` disables both `.claude` and `.agents` skill discovery (`cli.mdx`).

For polymorph:

- A coverage table would record `skills = ".agents/skills"`, with no link needed.
- Duplicate names are a live question. This repo has `.agents/skills` and `.claude/skills` pointing at the same store, so OpenCode finds every skill twice. The docs only say to "ensure skill names are unique across all locations"; what happens on a duplicate is not stated and not tested.
- A skill name that fails the regex (uppercase, underscores, `--`) is not loaded. Claude Code is looser here, so `inventory` could report this as a gap.

## Config and permissions

- Config files are `opencode.json` or `opencode.jsonc`. They are merged, not replaced, and later sources win for conflicting keys (v1 `config.mdx`). The order is: remote `.well-known/opencode`, global `~/.config/opencode/opencode.json`, `OPENCODE_CONFIG`, project `opencode.json`, `.opencode/` directories, `OPENCODE_CONFIG_CONTENT`, and managed config last.
- `.opencode/` subdirectories are plural (`agents/`, `commands/`, `modes/`, `plugins/`, `skills/`, `tools/`, `themes/`); singular names are accepted for backwards compatibility (v1 docs). Observed v2.0.32 did not read singular `plugin/`.
- `permission`: `allow`, `ask`, or `deny`, per key or `"*"`. Keys are `read`, `edit` (covers `edit`, `write`, `apply_patch`), `glob`, `grep`, `bash`, `task`, `skill`, `webfetch`, `websearch`, `question`, `lsp`, `todowrite`, `external_directory`, and `doom_loop`. Object syntax matches the tool input (`"bash": { "rm *": "deny" }`) with `*` and `?` wildcards, and the **last matching rule wins**.
- `OPENCODE_PERMISSION` inlines permissions as JSON. The legacy `tools` boolean config is deprecated since v1.1.1.

A static `permission.bash` deny list is a second way to express a guard: data in `opencode.json`, not code. It covers only pattern-shaped rules, not a script's logic. Whether polymorph generates it is a design decision, not recorded here.

## Headless runs

- v1 docs: `opencode run [message..]` takes `--auto`, `--model provider/model`, `--agent`, `--format json`, `--dir`, `--attach URL`, `--continue`, `--session`, and `--port`.
- polymorph's `opencode` capture driver runs `opencode run --standalone --auto PROMPT`, from `opencode run --help` on v2.0.32. The v1 docs list no `--standalone`. The driver has not been run against OpenCode.
- `OPENCODE_PURE` (like `--pure`) runs without external plugins, and `OPENCODE_DISABLE_DEFAULT_PLUGINS` disables the default ones. Either would silently disable a generated guard plugin, so a module should name them where it explains enforcement.

## What a module writer should settle first

1. **Which API**: v1 hooks object or v2 `setup(ctx)`. Name the targeted version in the mapping's header and in `verified`, when that is earned.
2. **Delivery**: OpenCode has no command hooks, so `generate` would have to write a plugin file. That is new (`generate` writes JSON configs today), and a generated file that executes code is exactly what the trust model guards. The plugin must be a shipped built-in template with constants rendered in, like `src/polymorph/data/opencode-discovery-plugin.js`, never code assembled from mapping content.
3. **Scoping**: filter hook calls to the repo, because the bus is server-wide.
4. **The deny route**: throw (v1), or `permission.hook("evaluate")` (v2). Probe it live, one marker per route, before any capability flag is `true`.
5. **Coverage table**: `memory = ["AGENTS.md"]` and `skills = ".agents/skills"` are documented; record them from the docs with that basis, then confirm them live.

## Open questions

Nothing below is answered by the docs or the source read here. Each needs a live run, or a closer read of OpenCode's core source, before a module relies on it.

- **Plugin layout under v1.** The v1 docs put plugin files directly in `.opencode/plugins/`. v2.0.32 loaded only child directories (`plugins/NAME/index.js`) and ignored bare files. Does a v1 build load the directory form, so one generated layout works for both?
- **Which API the shipped binary speaks.** The public docs are v1 and the observed build is v2. Does a current release accept both plugin shapes, or only one? And how does a module tell, from `opencode --version` or the config, which shape to generate?
- **What the model sees on a block.** v1 blocks by throwing from `tool.execute.before`. Is the error message shown to the model, as Claude Code's exit-2 stderr is, and does the step continue? Does a v2 throw behave the same?
- **Does a throw block at all in v2?** It was seen only in the binary, and `permission.hook("evaluate")` is the documented route. Which of the two should the deny route use?
- **Can a tool hook ask?** Neither version says how a hook raises an `ask`. Without one, every guard ask has to be hardened to deny in the hook.
- **Duplicate skill names.** `.agents/skills` and `.claude/skills` pointing at the same store make OpenCode find each skill twice. Is one dropped quietly, are both listed, or is it an error? The answer decides whether `equip` should skip the `.claude/skills` link for an OpenCode target.
- **`CLAUDE.md` precedence per directory.** "The first matching file wins in each category", but is that checked per directory while walking up, or across the whole walk? It matters for a repo with `CLAUDE.md` at the root and `AGENTS.md` in a subdirectory.
- **Repo scoping.** The bus is server-wide. The plugin's setup context names a place: `directory` and `worktree` in v1, and the shipped discovery plugin reads `ctx.location` in v2. Is that the session's repo or the server's start directory? The v2 `execute.before` event itself carries `sessionID` and `agent` but no directory. Can a generated guard tell, per call, that the call belongs to its repo?
- **`opencode run --standalone`.** It is in v2.0.32's `--help` and absent from the v1 docs. Is it still accepted? The capture driver depends on it.
- **`execute.after` and output rewriting in v1.** The v1 docs list `tool.execute.after` without a shape. What does it receive, and can it change what the model sees?
- **Subagents and MCP tools.** Do tool hooks fire for calls made inside a subagent session, and for MCP tools? Neither was exercised.
- **The Claude Agent provider's hooks.** The draft spec says OpenCode installs two Claude Code hooks of its own around each tool call. Do OpenCode plugin hooks still fire for Claude's calls, as the spec implies by running them "like any other model's tool call"? That needs a live run on a release that ships the provider.

## Not covered

The Claude Agent provider's behavior (draft, unreleased), MCP tool hooks, subagent sessions, Windows, and any OpenCode version other than v2.0.32 (the one observed) and the docs at commit `2fdc064`.
