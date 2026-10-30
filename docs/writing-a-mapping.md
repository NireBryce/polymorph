# Writing a mapping

A mapping describes one harness. Put it at `.agents/polymorph/mappings/NAME.toml` in your
repo to use it there, or in `mappings/NAME.toml` of a polymorph checkout to ship it (see
[CONTRIBUTING](../CONTRIBUTING.md)). The filename, without `.toml`, must equal the `name`
field, and `name` must match `[a-z][a-z0-9_-]*`.

If you do not know how the harness behaves, do not guess: capture it with
[Discovery](discovery.md) first and start from `discover draft`. Every value you set is a
claim that a wrong answer can turn into a hook that never fires.

Validate as you go:

```sh
python -m polymorph validate .agents/polymorph/mappings/mytool.toml
```
```
.agents/polymorph/mappings/mytool.toml: ok
```
(`validate` on the shipped `zcode.toml` was run; the path above is illustrative.)

## Worked example: `zcode`

This is `mappings/zcode.toml`, annotated. ZCode accepts the same hook protocol as Claude
Code, so almost every field is identity.

```toml
schema = 1
name = "zcode"
verified = "2026-10-30, from nixos-configs hook comments (payload shape, ask-as-allow); not a live run"
config_path = ".zcode/config.json"
format = "json"
delivery = "direct"
template = "claude-like"
context_file = "AGENTS.md"
skills_dir = ".agents/skills"
```

| Field | Required | What it does |
|---|---|---|
| `schema` | yes | Must be the integer `1`. |
| `name` | yes | Harness name; `--harness zcode` and `enabled = ["zcode"]` refer to it. |
| `verified` | no | Free text saying what was verified, when and how. **Absent means unverified**: `generate` warns, `check` raises `mapping-unverified`. Do not add it to a draft. Write what was actually checked, as the zcode one does ("from hook comments ... not a live run"). |
| `config_path` | yes | Where the harness reads hooks, relative to the repo root. No leading `/`, no `..`. |
| `format` | yes | `json`, `toml` or `yaml` pass validation, but `generate` only writes `json`. |
| `template` | yes | The shipped writer: `claude-settings` (`hooks.<Event>`, as Claude Code) or `claude-like` (`hooks.enabled` plus `hooks.events.<Event>`, per-hook `enabled: true`, as ZCode). Templates are named code in polymorph, never code from the mapping. A harness whose file looks like neither needs a new template, which is a code change. |
| `delivery` | no | `direct` or `adapter` (default). See [Concepts](concepts.md#delivery-direct-and-adapter). `direct` is rejected unless every translation is identity. |
| `context_file` | no | The file the harness reads for project instructions (`AGENTS.md`, `GEMINI.md`). Validated as a path, recorded, not used yet. |
| `skills_dir` | no | Where the harness reads skills. Same: recorded, not used yet. |

Unknown top-level keys are an error.

```toml
[tools]
shell = "Bash"
edit = ["Edit", "Write", "MultiEdit"]
read = ["Read", "Grep", "Glob"]
```

`[tools]` maps a canonical class (`shell`, `edit`, `read`, `mcp`, `other`) to the
harness's native tool names, as a string or a list. This is what `generate` turns into a
`matcher`: a hook scoped to the `edit` class becomes the matcher `Edit|Write|MultiEdit`.
It also lets the adapter turn a native name back into a Claude one (a native `run_shell_command`
becomes `Bash` when the script reads `tool_name`). A class you leave out has no equivalent:
a hook that needs it is reported as `hook-no-equivalent` and, if none of its classes exist,
left out of the generated file. Leave a class out rather than guess a name. Names are
plain identifiers (letters, digits, `_`, `.`, `*`, `-`).

```toml
[events]
session_start = "SessionStart"
pre_tool = "PreToolUse"
post_tool = "PostToolUse"
```

`[events]` maps the three canonical events to native event names. Leave out one the
harness lacks; hooks on it are reported as gaps rather than written.

```toml
[capabilities]
ask = false
post_event = true
exit_2_blocks = true
rewrites_input = false
json_answers = true
```

All five are required booleans. They become the `GUARD_*` variables (table in
[Concepts](concepts.md#capabilities-and-guard_-variables)). zcode sets `ask = false`
because ZCode treats an `ask` answer as allow, per the hook comments the mapping was
written from. When unsure, say `false`: it makes guards stricter, not looser. Draft
mappings from `discover draft` set all five `false` for that reason.

```toml
[payload_in]
tool_name = "tool_name"
"shell.command" = "tool_input.command"
"edit.file_path" = "tool_input.file_path"
cwd = "cwd"
session_id = "session_id"
```

`[payload_in]` says where, in the harness's payload, to find each canonical field. The
left is a canonical key; the right is a dotted path into the native JSON (list indexes are
allowed as numbers: `args.0.path`). The adapter builds the canonical payload from these.

Accepted canonical keys: `tool_name`, `cwd`, `session_id`, `tool_response`, `event`, and
`CLASS.field` for a class in the tool list (`shell.command`, `edit.file_path`,
`read.file_path`, `read.path`, `read.pattern`, `read.glob`), plus `extractor`. The adapter
places `shell.command` at `tool_input.command`, `edit.file_path` and `read.file_path` at
`tool_input.file_path`. Other `CLASS.field` keys are accepted by validation, but the
adapter currently stores them at that dotted name (for example `read.path`) rather than
under `tool_input`; see [Limits](limits.md#known-gaps). Missing source values are skipped,
not errors. `tool_response`, `session_id` and `cwd` are copied through if present even
when unlisted.

`extractor = "apply_patch_paths"` (optional) names a built-in that pulls file paths out of
a patch-style edit tool's text (`*** Update File: path` lines) into `tool_input.file_path`
and `tool_input.file_paths`. The registry is fixed in code; an unknown name fails
validation.

```toml
[payload_out]
```

`[payload_out]` says how to render the hook's answer for the harness. Keys are the answer
kinds `allow`, `deny`, `ask`, `warn` (extra context to the model) and `remind` (a system
message). Each value is a table with any of `exit` (integer), `stdout` and `stderr`
(strings). Strings may use `{reason}`, `{context}` and `{message}`; any other placeholder
is an error. The output is built as data and JSON-encoded, never pasted into a string.
zcode has no entries because it reads Claude's answers unchanged: with none, and
`json_answers = true`, the adapter passes the canonical answer through.

A harness that blocks by exit code, like the draft `gemini` mapping, looks like this:

```toml
[payload_out]
deny = { exit = 2, stderr = "{reason}" }
ask = { exit = 2, stderr = "{reason}" }
```

How the adapter classifies the script's answer: a `permissionDecision` of `allow`, `ask` or
`deny`; else `decision: "block"` counts as deny; else `additionalContext` is `warn`;
else `systemMessage` is `remind`; anything else is "no answer" (exit 0, no output). A kind
with no `payload_out` entry falls back to pass-through (JSON harness), exit 2 plus stderr
(deny on an exit-2 harness), or stderr text only.

`validate` also rejects code-like content in these strings: `$(`, `${`, backticks, `;`,
`|`, `&&`, `>`, `<`, backslashes, newlines and `..` (placeholders excepted).

## Mappings for a harness that has hooks but you have not seen them

Start from the draft:

```sh
python -m polymorph discover draft .polymorph-discovery/mytool.jsonl --name mytool > mytool.toml
```

then fix what its comments say to confirm (`config_path`, `template`, classes marked
`guessed from name`, unclassified tools), fill in `[capabilities]` by testing, and add
`[payload_out]` if the harness needs answers translated. A draft has empty
`[payload_out]`, which `validate` accepts; leave `delivery` unset until you know the
harness is genuinely identity.

## Fixtures

A fixture is one recorded exchange, replayed offline through the adapter to catch
regressions in the mapping. They live in `fixtures/NAME/CASE.json` in a polymorph checkout:

```json
{
  "native_in":    { "tool_name": "Bash", "tool_input": {"command": "git reset --hard"}, "...": "..." },
  "canonical_in": { "tool_name": "Bash", "tool_input": {"command": "git reset --hard"}, "...": "..." },
  "hook_answer":  { "hookSpecificOutput": { "permissionDecision": "deny", "permissionDecisionReason": "..." } },
  "native_out":   { "stdout": { "hookSpecificOutput": { "...": "..." } }, "exit": 0 }
}
```

- `native_in`: what the harness sends.
- `canonical_in`: what the adapter must turn it into. Compared exactly.
- `hook_answer`: what a hook prints, or `null` to test only the input side.
- `native_out`: what the adapter must emit. `stdout` is a JSON value (compared parsed), a
  string (compared verbatim), or empty/`null` for no output; `exit` is an integer.

Unknown or missing keys fail with a shape error. The shipped mappings have four fixtures
each: `deny`, `ask`, `warn`, `remind` (`gemini` has none, and `check` warns
`fixtures-missing`). Replay them:

```sh
python -m polymorph conformance --harness zcode
```
```
4 passed, 0 failed
```

Two limits. Fixtures run against the mapping's `translate_in` and `translate_out`, not the
hook script or a live harness. And they check the mapping you shipped against the output
you wrote down; if you wrote down the wrong output, they pass. A fixture built from a real
capture is worth more than one written by hand, so note in `verified` which kind it is.
`conformance` only knows the built-in `fixtures/` tree; `check` reports repo-local mappings
as `fixtures-skipped`.
