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
| conformance | `src/polymorph/conformance.py`, `validate.py`, `src/polymorph/fixtures/**`, `src/polymorph/mappings/**` | `validate_mapping`, `run_fixtures` |
| report | `.agents/skills/report-harness/SKILL.md`, `.github/ISSUE_TEMPLATE/harness-report.yml` | none |

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
delivery = "direct"                        # optional: "adapter" (default) or "direct"; see below
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

`delivery` (optional, default `"adapter"`): how `generate` writes the hook command.
`"adapter"` emits `python -m polymorph adapter --harness NAME -- "$CLAUDE_PROJECT_DIR/<script>"`.
`"direct"` emits just `"$CLAUDE_PROJECT_DIR/<script>"`, for a harness that already speaks the
canonical protocol. It is only valid for an identity translation: `payload_out` empty and every
`payload_in` value equal to the canonical Claude path (`tool_name`, `cwd`, `session_id`,
`shell.command` and `edit.file_path` -> `tool_input.command`/`tool_input.file_path`,
`read.file_path` -> `tool_input.file_path`, `read.path`/`read.pattern`/`read.glob` ->
`tool_input.path`/`.pattern`/`.glob`). Any other `payload_in` key (including `extractor`) or
a non-empty `payload_out` makes `delivery = "direct"` a validation error (`polymorph check`
reports `mapping-invalid`).

Required: `schema`, `name`, `config_path`, `format`, `template` and the tables `[tools]`,
`[events]`, `[capabilities]` (all five keys), `[payload_in]`, `[payload_out]`; an empty table
is valid. `generate`, `validate` and `check` apply the same rule (loading a mapping runs
`validate_mapping`), so a mapping missing one is refused by all three. Optional: `verified`,
`context_file`, `skills_dir`, `delivery`.

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
  decisions/NAME.toml  # answers to generation-time mismatches; see "Decisions"
  generated/NAME/      # generated shims and plugin files (planned)
```

Generated harness configs stay where the harness reads them (the mapping's `config_path`).
Discovery logs go to `.polymorph-discovery/` (by default under the current directory, or
`--log-dir`). polymorph never edits `.gitignore`: add `.polymorph-discovery/` yourself so a
raw log is not one `git add` away from being committed.

## Trust

Hook scripts must resolve under `<repo>/.agents/hooks/`. Extractors and templates are
looked up by name in code shipped with polymorph. `--trust-dir` is required to load
mappings from outside the repo's `.agents/polymorph/mappings/` or the built-in `src/polymorph/mappings/`.

## CLI

```
polymorph generate [--repo .] [--harness NAME ...] [--check] [--mappings DIR] [--trust-dir DIR ...]   # --check: exit 1 on drift
polymorph adapter --harness NAME [--mappings DIR] [--trust-dir DIR ...] -- SCRIPT
polymorph validate FILE.toml
polymorph conformance [--harness NAME]
polymorph check [--repo .] [--harness NAME ...] [--format text|json|sarif] [--strict]
polymorph discover init --harness NAME --config-path P [--event-name E] [--matcher M]
                        [--shape claude-settings|claude-like] [--log-dir DIR] [--full] [--env-be-very-careful]
                        # adds the logger as its own matcher group; keeps other hooks; refuses unparsable hooks
polymorph discover log [--full] [--env-be-very-careful] --out FILE   # stdin -> one JSON line; silent, exit 0 always
polymorph discover summary FILE.jsonl [--include EVENT ...] [--event EVENT ...]
                        # per-event/per-tool key tables, counts, sessions; tool events by default, sorted by ts, paired by id
polymorph discover reload-check --config-path P --scratch-log F [--finish]
                        [--event-name E] [--matcher M] [--shape claude-settings|claude-like]
polymorph discover draft FILE.jsonl --name NAME [--include EVENT ...] [--event EVENT ...]
                        # prints draft mapping, no `verified`
polymorph capture --harness NAME [--yes] [--keep] [--steps STEP ...] [--step-timeout S] [--total-timeout S]
                  [--out DIR] [--full]   # runs a real harness headless in a scratch dir; typed `yes` unless --yes
polymorph capture --print-prompts        # prints the manual prompt kit, runs nothing
```

Discovery log line: `{schema (1; older lines lack it and still read), ts, event, tool_name, session_id, env_keys (empty unless `--env-be-very-careful`; then PROJECT/WORKSPACE/HARNESS-like names only), redacted, raw
(parsed JSON or text), keys, tool_input_keys}`; event/tool_name/session_id are null when absent.
By default every value in `raw` except structural ones (event, tool name, ids that look like ids, permission mode) becomes `{type, length}`; output-like objects (`tool_response`, `output`, `result`) keep nested key names to three levels with `{type, length}` leaves; `--full` logs raw payloads.
Logger failures go to `<name>.errors.jsonl` beside the log (best effort; exit 0, no stdout).
The default log holds full commands and file contents and must not be shared.
The log is untrusted data: draft copies back only values matching strict name patterns,
never command text or paths, and omits what it did not observe. Comments carry evidence
counts, `guessed from name` marks, unclassified tools, and inferred config_path/template. Owner: `discover.py`.

Generate output lists, per target: unmapped settings, the questions for undecided mismatches,
applied decisions (`weakened:` for the weakening ones), stale decisions and notes. Exit codes of
`generate`: 0 ok, 1 `--check` found drift, 2 usage or internal error (including an invalid
decision file), 3 undecided mismatches.

## Decisions

When a target harness has more or fewer fields or events than a hook needs, `generate` does
not guess and does not drop the hook silently. Each mismatch is *undecided* until
`.agents/polymorph/decisions/NAME.toml` answers it. The file is data: validated, never executed.

```toml
schema = 1

[decisions.".agents/hooks/edit-check.sh:no-event"]   # id = <hook-script>:<kind>
choice = "substitute:BeforeTool"
date = "2026-10-30"
note = "optional free text"
```

Top-level keys are `schema` (must be 1) and `decisions`; unknown keys are errors. An entry has
`choice` and `date` (`YYYY-MM-DD`) and may have `note`. The id is the hook script path relative
to the repo root, a colon and the kind. One decision covers every hook of that script that has
that mismatch in that target.

| Kind | Fires when | Choices |
|---|---|---|
| `no-event` | the hook's canonical event has no entry in the mapping's `[events]` | `skip` (hook not generated); `substitute:<native event>` (register it on that event; the name must be a value of the mapping's `[events]`) |
| `no-tool-class` | a class the hook is scoped to has no entry in `[tools]` | `skip` (hook is registered for the remaining classes; not at all if none remain) |
| `missing-field` | the hook is scoped to a class whose payload field the mapping's `[payload_in]` does not map | `skip` (hook not generated); `run-empty` (generated; the hook sees the field empty) |

Which fields a hook reads is modelled by tool class, not by scanning the script: `shell` reads
`shell.command`, `edit` reads `edit.file_path` (an `extractor` counts), `read` reads any of
`read.file_path`, `read.path`, `read.pattern`, `read.glob`; `mcp`, `other` and a hook with no
class read none. `missing-field` is not checked for `delivery = "direct"` mappings, whose
hooks get the Claude payload unchanged.

Undecided means: `generate` writes nothing for that target, prints each item as a block

```
  question: <id>
    hook:    <script> (<event>, classes <c,...>)
    problem: <what the target lacks>
    options: <choice> | <choice>
    default: skip
    answer:  in .agents/polymorph/decisions/NAME.toml add [decisions."<id>"] with choice = ..., date = ...
```

and exits 3. Other targets are still written. polymorph never prompts; an agent relays the
questions to its user and writes the answers. With `--check`, undecided items also exit 3.

Once decided, `generate` applies the choice and prints `decided:` or, for a weakening one,
`weakened:` per id. Weakening means `run-empty`, or a `substitute` whose native event belongs to
a different canonical event than the hook's own (always so, since the hook's event is the one
that is missing). `check` reports it as `decision-weakens-guard`. A decision whose id matches
nothing now is `decision-stale`. An invalid entry is `decision-invalid` and makes `generate` exit
2 before writing.

The decision is recorded in the decision file and in the `generate` output only. It is not
written into the generated harness config: that format is JSON, which has no comments, and a
`_polymorph` key in a harness's settings file is not known to be tolerated by any shipped
target (none is verified for unknown top-level keys), so none is added.

Extra payload fields a mapping maps that no hook reads are printed once per target as
`note:`, never as a question.

## Fixtures

`src/polymorph/fixtures/<harness>/<case>.json`:
`{"native_in": {...}, "canonical_in": {...}, "hook_answer": {...}|null, "native_out": {"stdout": ..., "exit": N}}`
covering deny, ask, warn(additionalContext), remind(systemMessage) for each mapping.

The `gemini` mapping is an unverified draft written from a sketch, not from a captured session,
so it has no fixtures; `check` reports `fixtures-missing` for it until a harness report supplies
real ones. Invented fixtures would only restate the guess.

## polymorph check

`polymorph check [--repo .] [--harness NAME ...] [--format text|json|sarif] [--strict]`
(`check.py`). Static: never executes a hook script, a harness or the network; writes nothing.
Finding: `{severity: error|warning|note, code, target|null, message, file|null}`.

| Code | Severity | Fires when |
|---|---|---|
| `no-targets` | error | nothing enabled and no `--harness` |
| `settings-invalid` | error | `.agents/settings.json` missing or not a JSON object |
| `mapping-invalid` | error | mapping fails `validate_mapping`, or cannot be loaded |
| `mapping-unverified` | warning | mapping has no `verified` |
| `mapping-untested-live` | note | every target: block semantics, ask enforcement unproven |
| `hook-outside-hooks-dir` | error | hook command is not a bare script resolving under `.agents/hooks/` |
| `hook-missing` / `hook-not-executable` | error | script absent / lacks the execute bit |
| `decision-undecided` | error | a hook needs an event, tool class or field the target lacks and no decision answers it |
| `decision-weakens-guard` | warning | an applied decision is `run-empty`, or a `substitute` whose event is not the hook's own |
| `decision-stale` | warning | a decision id matches no mismatch now |
| `decision-invalid` | error | the decision file or an entry fails validation |
| `setting-unmapped` | note | `canon.unmapped` entries |
| `generated-drift` | error | generated file differs from `generate` output; a missing file is drift |
| `fixtures-missing` | warning | built-in mapping has no `src/polymorph/fixtures/<name>/*.json` |
| `fixtures-skipped` | note | repo-local mapping: fixtures not checked |
| `fixture-failed` | error | a fixture does not replay (via `polymorph.conformance`) |

Exit: 0 no errors (and, with `--strict`, no warnings); 1 findings fail; 2 usage or internal
error. `text`: grouped by target, `severity code target: message`, then a summary line.
`json`: `{findings, summary: {error, warning, note}}`. `sarif`: SARIF 2.1.0, driver `polymorph`,
rules from the codes seen, `artifactLocation.uri` when `file` is known. Hook-script and
drift checks need a readable settings file; if a hook command is rejected, canon-derived
checks (decisions, unmapped, drift) are skipped for that run.

## Phase scope

Built now: phases 0-3 (schema, canon + Claude reader, `zcode` and `claude` mappings,
capability env, adapter, conformance), plus the report-harness skill (self-report mode).
Not built: discovery mode capture beyond the logger generator, code-generating targets,
plugin packaging, edits to nixos-configs hooks (the capability swap is a separate PR there).
