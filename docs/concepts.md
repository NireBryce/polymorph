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
| `event` | `session_start`, `pre_tool`, or `post_tool`. Claude's `SessionStart`, `PreToolUse`, `PostToolUse`. |
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

A missing, empty, or `*` matcher means all tools. `session_start` hooks are always all.

Everything the reader cannot translate is listed as **unmapped** and reported by `generate`
and `check`: any top-level key other than `hooks` (`attribution`, `model`, ...), each
`permissions.*` sub-key, hook events other than the three above (`Stop`, ...), and hooks
whose `type` is not `command`. Nothing is dropped silently. In particular, **Claude's
`permissions` allow/deny rules are never carried to other harnesses.**

## Mappings

A mapping is a TOML file, `schema = 1`, that says how one harness differs from the
canonical shape: where its config lives, what its tools and events are called, what it can
do, how its payloads are laid out, and how its answers must look. Field by field in
[Writing a mapping](writing-a-mapping.md).

Where mappings are looked up, in order:

1. `--mappings DIR`, only if DIR is the repo's `.polymorph/basecamp/mappings`, the built-in
   `mappings/`, or listed with `--trust-dir`. Otherwise it is refused.
2. The repo's `.polymorph/basecamp/mappings/NAME.toml`. If a built-in of the same name
   exists, the repo one wins and a warning is printed.
3. The built-in `src/polymorph/mappings/NAME.toml` shipped with polymorph.

A mapping's `name` must equal its filename (without `.toml`).

Mappings are data. Expressions are dotted paths and `{placeholder}` substitution only; no
field is ever executed, shell-expanded, or loaded as code. Unknown keys are an error.

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
python3 -m polymorph adapter --harness gemini -- "$CLAUDE_PROJECT_DIR/.agents/hooks/block-rm.sh"
```

This needs `python3` on the harness's `PATH` with `polymorph` importable, and the harness
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
non-JSON output, a timeout, or an internal error produces a visible message,
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
adapter runs; a script that needs them must default sensibly when they are unset. A direct
mapping that also sets `ask = false` is therefore flagged by `check` as
`direct-ask-unenforceable`: the hardening the flag promises is unobservable by the hook,
which can only apply it if it was written for this harness and detects it itself.
An adapter mapping that cannot express a block at all (no `payload_out.deny`, and
`exit_2_blocks` and `json_answers` both false) fails `validate`, so `check` reports it as
`mapping-invalid`: a deny would otherwise become a visible NOT-checked fault and the call
would proceed.

Capabilities in a mapping are claims. Only a live test establishes them; see
[Limits](limits.md).

## Trust model

- Hook scripts must resolve (after `realpath`, so symlinks cannot escape) under
  `<repo>/.agents/hooks/`. The reader, `check`, and the adapter all enforce it.
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

## Decisions

Harnesses differ: one may have no post-tool event, no tool for a class, or no payload field
a hook reads. polymorph does not guess a substitute and does not drop the hook quietly.
`generate` stops for that target (exit 3), writes nothing for it, and prints each item as a
question with its options. The answer goes in `.polymorph/basecamp/decisions/NAME.toml`, keyed
by `<hook-script>:<event>:<kind>` -- the hook's canonical event is part of the key, so a
script registered under several events is decided per event -- with a `choice` and a `date`.
Skipping is always a choice you
record, and the choices that make a guard weaker (`run-empty`, moving a hook to another
event) are flagged by `generate` and by `polymorph manage check`. Details: [SPEC](SPEC.md#decisions).

## Inventory and the gap report

Beyond the transliterate kinds (settings, hooks), the reader's job, polymorph discovers
the **discover-and-report** kinds: skills (`.agents/skills/`), rules (`.claude/rules/`,
`.agents/rules/`), memory (`AGENTS.md`, `CLAUDE.md`) and the entries of `.claude/`. They
are reported as paths and symlink targets -- contents are never opened, and nothing found
is dropped. `polymorph inventory` prints that surface plus each harness's **gap report**:
the provided paths the harness does not read, which it will silently ignore even after
equipping: kinds it never reads (`unsupported`), and paths it reads that are missing
(`no-link`), a second real copy (`unlinked-copy`, the drift between `AGENTS.md` and
`CLAUDE.md`, or `.agents/` and `.claude/`) or a symlink to somewhere else (`points-elsewhere`).
A symlink whose resolved target is already honoured content is a pointer, not a gap:
`.claude/skills` -> `.agents/skills` when the table honours skills at `.agents/skills`, or
`CLAUDE.md` -> `AGENTS.md` when the table honours `AGENTS.md` -- the harness reads those
bytes through the other path, so the report leaves them out. A real second copy stays a
gap: the pointer rule needs a symlink.

What a harness honours is the **coverage table**, `coverage/NAME.toml` -- data with the
same character as the mappings, not the mapping's `[capabilities]` flags (those say how a
hook behaves; the coverage table says what the harness reads). A harness with no coverage
table is *unknown*: the report says so instead of claiming gaps. See
[the CLI reference](cli.md#inventory).

## The env contract

A loadout includes how a session runs, not only which files exist: `polymorph env` turns
host facts in `.polymorph/basecamp/env.toml` (the machine key's path, the sockets a
session must not inherit, per-harness session markers) into shell exports, or runs a
command under them. It is **emit-only** — the key is a path, never opened; nothing is
written; no daemon. Harness names come from the host facts and the mappings, so the same
contract works for any harness the repo equips. See [the CLI reference](cli.md#env).

## The equip run

`polymorph equip --harness NAME` is the onboarding run: it writes the harness's config
files (`manage generate`'s output), reports the honoured surface and the gap report from
[the inventory](#inventory-and-the-gap-report), emits the [env contract](cli.md#env)
lines, creates the symlinks the harness needs to read what the repo provides
(`CLAUDE.md -> AGENTS.md` for Claude Code), and records a provenance manifest
(`loadout.toml`: per issued artifact its sha256 and source, per symlink its target). Posture-aware: a committed basecamp gets committed artifacts; in a
contributor checkout the artifacts are regenerable and `--gitignore` appends a marked,
idempotent section to `.gitignore`. See [the CLI reference](cli.md#equip).

## What polymorph stores in a repo

```
.polymorph/basecamp/
  config.toml            enabled = ["zcode", ...]
  mappings/NAME.toml     repo-local mapping; overrides a built-in of the same name, with a warning
  coverage/NAME.toml     what one harness honours of the discover-and-report kinds (see [Inventory](#inventory-and-the-gap-report))
  decisions/NAME.toml    answers to generation-time mismatches (see below)
  loadout.toml           equip's provenance manifest: per issued artifact its sha256 and source; per symlink made its target
  generated/NAME/        planned, not built
```

`config.toml` accepts only the key `enabled`; anything else is an error. Generated harness
configs are not stored here: they are written where the harness reads them, at the
mapping's `config_path` (`.zcode/config.json`, `.claude/settings.json`, ...). Discovery
logs live in `.polymorph/discovery/` in the directory you ran `discover init` from, kept
out of `.agents/` so a raw log is never inside a tree you are likely to commit. polymorph
does not add that folder to your `.gitignore`; do it yourself (see
[Discovery](discovery.md#privacy)).
