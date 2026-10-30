# polymorph spec (schema 1)

Origin: NireBryce/nixos-configs issue 597. polymorph reads a repo's Claude Code agent
config (`.agents/settings.json`, `.agents/hooks/`) and produces hook configs for other
coding harnesses, driven by declarative per-harness TOML mappings. Readers in, mappings
out; no control flow in mappings. Python 3.11+, stdlib only (`tomllib`, `json`, `argparse`).

## Module ownership (parallel work; do not edit another's files)

| Area | Files | Contract it exposes |
|---|---|---|
| core | `src/polymorph/canon.py`, `reader_claude.py`, `mapping.py`, `generate.py`, `cli.py`, `__main__.py` | `Canon`, `load_mapping`, `generate` |
| adapter | `src/polymorph/adapter.py`, `extractors.py`, `exprs.py` | `translate_in`, `translate_out`, `run_adapter` |
| conformance | `src/polymorph/conformance.py`, `validate.py`, `fixtures/**`, `mappings/**` | `validate_mapping`, `run_fixtures` |
| report | `skills/report-harness/SKILL.md`, `.github/ISSUE_TEMPLATE/harness-report.yml` | none |

## Canonical config (`canon.py`)

```python
@dataclass(frozen=True)
class Hook:
    event: str          # "session_start" | "pre_tool" | "post_tool"
    classes: tuple[str, ...]   # subset of shell, edit, read, mcp, other; () = all (session_start)
    script: str         # path relative to repo root, must resolve under hooks_dir
    timeout: int | None

@dataclass(frozen=True)
class Canon:
    hooks: tuple[Hook, ...]
    unmapped: tuple[str, ...]   # human-readable settings keys with no cross-harness meaning
```

Claude reader: matcher regex `Bash`->shell, `Edit|Write|MultiEdit`->edit,
`Read|Grep|Glob`->read, `mcp__*`->mcp, else other. `permissions.*` and `attribution`
are reported in `unmapped`, never dropped silently. A script outside `.agents/hooks/`
(after `realpath`) is refused with an error.

## Mapping file (TOML, `schema = 1`)

```toml
schema = 1
name = "zcode"
verified = "2026-10-30, live session"      # absent => generator warns
config_path = ".zcode/config.json"
format = "json"
template = "claude-like"                   # shipped writer: claude-like (hooks.enabled + hooks.events) or claude-settings (hooks.<Event>)
context_file = "AGENTS.md"
skills_dir = ".agents/skills"

[tools]                 # class -> native tool names (str or list)
shell = "Bash"
edit  = ["Edit", "Write", "MultiEdit"]
read  = ["Read", "Grep", "Glob"]

[events]                # canonical event -> native event; missing => reported gap
session_start = "SessionStart"
pre_tool = "PreToolUse"
post_tool = "PostToolUse"

[capabilities]
ask = false             # exported as GUARD_ASK_ENFORCED=0/1
post_event = true       # GUARD_POST_EVENT
exit_2_blocks = true    # GUARD_EXIT2_BLOCKS
rewrites_input = false  # GUARD_REWRITES_INPUT
json_answers = true     # GUARD_JSON_ANSWERS

[payload_in]            # canonical key = "native.dotted.path"
tool_name = "tool_name"
"shell.command" = "tool_input.command"
"edit.file_path" = "tool_input.file_path"
cwd = "cwd"
# extractor = "apply_patch_paths"   # optional; named built-in, never code

[payload_out]
deny = { exit = 2, stderr = "{reason}" }
```

Expressions are dotted paths and `{placeholder}` substitution only; output is
JSON-encoded, never string-pasted. Unknown keys are a validation error. No field is
ever executed, shell-expanded, or loaded as code.

## Wire protocol

Canonical = Claude Code hook protocol: stdin JSON with `tool_name`, `tool_input`,
`tool_response`, `cwd`, `session_id`; answers via `hookSpecificOutput.permissionDecision`
(`allow|ask|deny`) + `permissionDecisionReason`, `additionalContext`, `systemMessage`,
post-call `decision:"block"`, or exit 2 with stderr.

Adapter invocation: `python -m polymorph adapter --harness NAME [--mappings DIR] -- SCRIPT`.
Sets `GUARD_*` capability env vars, runs the script with the canonical payload, maps the
answer to native form. Translation failure => visible message (stderr + exit code the
harness shows to the model) and NEVER a deny; never fail open silently. `ask` on a harness
with `ask = false` is hardened to deny by the *hook* reading `GUARD_ASK_ENFORCED`, not by
the adapter.

## Repo layout (everything polymorph stores in a repo)

```
.agents/polymorph/
  config.toml          # enabled = ["zcode", ...]  (targets to generate)
  mappings/NAME.toml   # repo-local mapping; overrides a built-in of the same name, with a warning
  decisions/NAME.toml  # answers to generation-time questions (planned)
  generated/NAME/      # generated shims and plugin files (planned)
```

Generated harness configs stay where the harness reads them (the mapping's `config_path`).
Discovery logs stay in `.polymorph-discovery/` outside this tree, gitignored, so a raw log
is never one `git add` away from being committed.

## Trust

Hook scripts must resolve under `<repo>/.agents/hooks/`. Extractors and templates are
looked up by name in code shipped with polymorph. `--trust-dir` is required to load
mappings from outside the repo's `.agents/polymorph/mappings/` or the built-in `mappings/`.

## CLI

```
polymorph generate [--repo .] [--harness NAME ...] [--check]   # --check: exit 1 on drift
polymorph adapter --harness NAME -- SCRIPT
polymorph validate FILE.toml
polymorph conformance [--harness NAME]
polymorph discover init --harness NAME --config-path P [--event-name E] [--matcher M] [--full]
polymorph discover log [--full] --out FILE   # stdin -> one JSON line; silent, exit 0 always
polymorph discover summary FILE.jsonl      # per-event/per-tool key tables, counts, sessions
polymorph discover reload-check --config-path P --scratch-log F [--finish]
polymorph discover draft FILE.jsonl --name NAME   # prints draft mapping, no `verified`
```

Discovery log line: `{ts, event, tool_name, session_id, env_keys (empty unless `--env-be-very-careful`; then PROJECT/WORKSPACE/HARNESS-like names only), redacted, raw
(parsed JSON or text), keys, tool_input_keys}`; event/tool_name/session_id are null when absent.
By default every value in `raw` except structural ones (event, tool name, ids, permission mode) becomes `{type, length}`; `--full` logs raw payloads.
Logger failures go to `<name>.errors.jsonl` beside the log (best effort; exit 0, no stdout).
The default log holds full commands and file contents and must not be shared.
The log is untrusted data: draft copies back only values matching strict name patterns,
never command text or paths, and omits what it did not observe. Comments carry evidence
counts, `guessed from name` marks, unclassified tools, and inferred config_path/template. Owner: `discover.py`.

Generate output lists, per target, unmapped settings and hooks with no equivalent.

## Fixtures

`fixtures/<harness>/<case>.json`:
`{"native_in": {...}, "canonical_in": {...}, "hook_answer": {...}|null, "native_out": {"stdout": ..., "exit": N}}`
covering deny, ask, warn(additionalContext), remind(systemMessage) for each mapping.

## Phase scope

Built now: phases 0-3 (schema, canon + Claude reader, `zcode` and `claude` mappings,
capability env, adapter, conformance), plus the report-harness skill (self-report mode).
Not built: discovery mode capture beyond the logger generator, code-generating targets,
plugin packaging, edits to nixos-configs hooks (the capability swap is a separate PR there).
