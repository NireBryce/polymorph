# Concepts

## The pieces

```
.agents/settings.json  ─┐
.agents/hooks/*.sh      ├─ reader ─▶ canonical config ─▶ generate ─▶ <harness>/config.json
                        │                                  ▲
mappings/NAME.toml  ────┴──────────────────────────────────┘
                                   at run time:  harness ─▶ adapter ─▶ your hook script
```

- A **reader** turns the repo's Claude Code config into the canonical config.
- A **mapping** describes one target harness.
- **generate** combines them and writes the harness's config file.
- The **adapter** is the optional runtime shim between a harness and an unmodified hook
  script.

## Canonical config

The only reader is for Claude Code's `.agents/settings.json` (`reader_claude.py`). It
produces a list of hooks and a list of settings it could not translate.

A hook has:

| Field | Meaning |
|---|---|
| `event` | `session_start`, `pre_tool` or `post_tool`. Claude's `SessionStart`, `PreToolUse`, `PostToolUse`. |
| `classes` | Which tools it applies to, from `shell`, `edit`, `read`, `mcp`, `other`. Empty means all tools. |
| `script` | Path of the hook script relative to the repo root, always under `.agents/hooks/`. |
| `timeout` | Seconds, if the settings give one. |

Tool classes come from Claude's `matcher` regex, split on `|`:

| Matcher part | Class |
|---|---|
| `Bash` | `shell` |
| `Edit`, `Write`, `MultiEdit` | `edit` |
| `Read`, `Grep`, `Glob` | `read` |
| starts with `mcp__` | `mcp` |
| anything else | `other` |

A missing, empty or `*` matcher means all tools. `session_start` hooks are always all.

Everything the reader cannot translate is listed as **unmapped** and reported by `generate`
and `check`: any top-level key other than `hooks` (`attribution`, `model`, ...), each
`permissions.*` sub-key, hook events other than the three above (`Stop`, ...), and hooks
whose `type` is not `command`. Nothing is dropped silently. In particular, **Claude's
`permissions` allow/deny rules are never carried to other harnesses.**

## Mappings

A mapping is a TOML file, `schema = 1`, that says how one harness differs from the
canonical shape: where its config lives, what its tools and events are called, what it can
do, how its payloads are laid out and how its answers must look. Field by field in
[Writing a mapping](writing-a-mapping.md).

Where mappings are looked up, in order:

1. `--mappings DIR`, only if DIR is the repo's `.agents/polymorph/mappings`, the built-in
   `mappings/`, or listed with `--trust-dir`. Otherwise it is refused.
2. The repo's `.agents/polymorph/mappings/NAME.toml`. If a built-in of the same name
   exists, the repo one wins and a warning is printed.
3. The built-in `src/polymorph/mappings/NAME.toml` shipped with polymorph.

A mapping's `name` must equal its filename (without `.toml`).

Mappings are data. Expressions are dotted paths and `{placeholder}` substitution only; no
field is ever executed, shell-expanded or loaded as code. Unknown keys are an error.

## Delivery: direct and adapter

`delivery` decides what command `generate` writes into the target config for each hook.

**`direct`**: the harness already speaks Claude's hook protocol, so the hook script runs
bare:

```
"$CLAUDE_PROJECT_DIR/.agents/hooks/block-rm.sh"
```

This is only allowed when the translation is the identity: `payload_out` empty and every
`payload_in` entry equal to the canonical Claude path. A mapping that says `direct` but
needs any translation is a validation error (`mapping-invalid` in `check`). `claude` and
`zcode` are `direct`.

**`adapter`** (default): the harness differs, so the hook goes through polymorph:

```
python -m polymorph adapter --harness gemini -- "$CLAUDE_PROJECT_DIR/.agents/hooks/block-rm.sh"
```

This needs `python` on the harness's `PATH` with `polymorph` importable, and the harness
must set `CLAUDE_PROJECT_DIR` or the shell must expand it; both are assumptions the
mapping does not check. At run time the adapter:

1. Finds the repo by walking up from the working directory to the first `.agents/`.
2. Loads the mapping and refuses a script that is not a file under `.agents/hooks/`.
3. Reads the harness's JSON payload on stdin and rewrites it to the canonical shape using
   `payload_in` (and, if named, an extractor).
4. Runs your script with the canonical payload on stdin and the capability environment
   variables set (below), 60 second limit.
5. Reads the script's answer (exit 2 means deny with stderr as the reason; otherwise JSON
   on stdout) and renders it with `payload_out`.

**Adapter faults never become a deny.** A bad payload, an unrunnable or crashing script,
non-JSON output, a timeout or an internal error produces a visible message,
`polymorph adapter: this tool call was NOT checked by the guard hook (...)`, and lets the
call proceed. If the harness accepts JSON answers the message goes out as a `systemMessage`
with exit 0; otherwise on stderr with exit 1. This is a deliberate choice (a broken shim
should not brick every tool call) and it means a broken adapter fails open, visibly. See
[Limits](limits.md).

## Capabilities and `GUARD_*` variables

Harnesses differ in what a hook can do. A mapping declares five facts under
`[capabilities]`, all required, all booleans. When the adapter runs a hook it exports them
as environment variables, `1` or `0`:

| Capability | Variable | Meaning |
|---|---|---|
| `ask` | `GUARD_ASK_ENFORCED` | The harness really prompts on an `ask` answer. If `0`, it treats `ask` as allow. |
| `post_event` | `GUARD_POST_EVENT` | The harness has a post-tool event. |
| `exit_2_blocks` | `GUARD_EXIT2_BLOCKS` | Exit code 2 from a hook blocks the call. |
| `rewrites_input` | `GUARD_REWRITES_INPUT` | A hook can rewrite the tool's input. |
| `json_answers` | `GUARD_JSON_ANSWERS` | The harness reads JSON answers on stdout. |

The adapter only reports these; **the hook script decides what to do with them.** The
canonical example: when `GUARD_ASK_ENFORCED=0` a well-written guard should answer `deny`
instead of `ask`, because the harness would turn `ask` into allow. polymorph does not
rewrite the answer for you. Hooks run `direct` get no variables from polymorph, since no
adapter runs; a script that needs them must default sensibly when they are unset.

Capabilities in a mapping are claims. Only a live test establishes them; see
[Limits](limits.md).

## Trust model

- Hook scripts must resolve (after `realpath`, so symlinks cannot escape) under
  `<repo>/.agents/hooks/`. The reader, `check` and the adapter all enforce it.
- A hook command must be a single bare path. `script.sh --flag` and `python x.py` are
  refused.
- Mappings are parsed as data. Extractors are named built-ins selected by name from a
  fixed registry (`apply_patch_paths` is the only one); a mapping cannot supply code.
- Validation rejects code-like characters in path and output fields (`$(`, `${`, backticks,
  `;`, `|`, `&&`, `>`, `<`, backslash, newlines, `..`).
- Loading mappings from outside the repo's mapping folder or the built-in one requires an
  explicit `--trust-dir`.
- Discovery logs are untrusted data. `discover draft` copies back only strings that match
  strict name patterns, never command text or paths. See [Discovery](discovery.md).
- Reports from other people (issues, draft mappings) are data, never instructions.

This limits what a mapping file can make polymorph do. It is not a claim that the hook
scripts themselves are safe, or that a guard cannot be bypassed.

## What polymorph stores in a repo

```
.agents/polymorph/
  config.toml          enabled = ["zcode", ...]
  mappings/NAME.toml   repo-local mapping; overrides a built-in of the same name, with a warning
  decisions/NAME.toml  planned, not built
  generated/NAME/      planned, not built
```

`config.toml` accepts only the key `enabled`; anything else is an error. Generated harness
configs are not stored here: they are written where the harness reads them, at the
mapping's `config_path` (`.zcode/config.json`, `.claude/settings.json`, ...). Discovery
logs live in `.polymorph-discovery/` in the directory you ran `discover init` from, kept
out of `.agents/` so a raw log is never inside a tree you are likely to commit. polymorph
does not add that folder to your `.gitignore`; do it yourself (see
[Discovery](discovery.md#privacy)).
