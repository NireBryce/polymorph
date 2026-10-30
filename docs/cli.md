# CLI reference

Run as `python -m polymorph ...` or, after an install, `polymorph ...`. Exit codes: `0`
success; `1` the command ran and found a problem (drift, findings, failed fixtures, an
invalid mapping); `2` usage or internal error (`polymorph: error: ...` on stderr). The
adapter and `discover log` have their own rules, below.

## `generate`

```
polymorph generate [--repo .] [--harness NAME ...] [--check] [--mappings DIR] [--trust-dir DIR ...]
```

Writes each target's config file from `.agents/settings.json`.

| Flag | Meaning |
|---|---|
| `--repo PATH` | Repo root. Default `.`. |
| `--harness NAME` | Target; repeatable. Default: `enabled` in `.agents/polymorph/config.toml`. With neither, prints `no targets: ...` and exits 2. |
| `--check` | Write nothing. Exit 1 if any generated file differs from what is on disk (a missing file is drift). |
| `--mappings DIR` | Read mappings from DIR instead of the usual lookup. DIR must be the repo's mapping folder, the built-in one, or trusted. |
| `--trust-dir DIR` | Allow `--mappings DIR`. Repeatable. |

Output, per target: `[NAME] wrote|would write|unchanged PATH (DRIFT|ok)`, then indented
lines:

- `warning:` the mapping has no `verified` field.
- `gap:` a hook the target cannot run: no equivalent event, or no tool for a class.
- `unmapped setting:` a setting with no cross-harness meaning.

Other top-level keys already in the target file are kept; the whole `hooks` key is
rewritten. Hooks you added to that file by hand are lost. Invalid JSON in the existing
file is an error.

## `check`

```
polymorph check [--repo .] [--harness NAME ...] [--format text|json|sarif] [--strict]
```

Static analysis. Runs no hook, harness or network, writes no file. Exit 0 when there are
no errors (with `--strict`, no warnings either), 1 when findings fail, 2 on usage or
internal errors.

| Flag | Meaning |
|---|---|
| `--repo`, `--harness` | As for `generate`. No targets gives the `no-targets` error. |
| `--format` | `text` (default), `json` or `sarif` (SARIF 2.1.0, one result per finding, a location when the finding has a file). |
| `--strict` | Warnings fail the run too. |

Each finding has a severity (`error`, `warning`, `note`), a code, a target (or none), a
message and sometimes a file. Text output is `severity code target: message (file)`
grouped by target, then a summary line.

| Code | Severity | Means |
|---|---|---|
| `no-targets` | error | Nothing enabled and no `--harness`. |
| `settings-invalid` | error | `.agents/settings.json` missing or not a JSON object. |
| `mapping-invalid` | error | Mapping fails validation or cannot be loaded. |
| `mapping-unverified` | warning | Mapping has no `verified` field. |
| `mapping-untested-live` | note | Always, for every target: block and ask behaviour is unproven without a live test. |
| `hook-outside-hooks-dir` | error | A hook command is not a bare script under `.agents/hooks/`. |
| `hook-missing` / `hook-not-executable` | error | The script does not exist / lacks the execute bit. |
| `hook-no-equivalent` | warning | The target lacks an event or tool class a hook needs (same as `generate`'s `gap:`). |
| `setting-unmapped` | note | A setting was not translated. |
| `generated-drift` | error | A generated file differs from `generate`'s output, or is missing. |
| `fixtures-missing` | warning | A built-in mapping has no fixtures. |
| `fixtures-skipped` | note | A repo-local mapping: fixtures are not checked. |
| `fixture-failed` | error | A fixture did not replay. |

If a hook command is rejected, checks derived from the config (gaps, unmapped, drift) are
skipped for that run. `mapping-untested-live` is a note, so even `--strict` never makes it
fail: it is a standing reminder, not a result.

## `adapter`

```
polymorph adapter --harness NAME [--mappings DIR] [--trust-dir DIR ...] -- SCRIPT
```

The runtime shim. Generated configs call it; you rarely do, except to test by hand.
Reads the harness's JSON payload on stdin, runs SCRIPT (which must be a file under the
repo's `.agents/hooks/`; the repo is the nearest ancestor of the working directory with an
`.agents/` folder), and writes the harness-shaped answer to stdout/stderr and exits with
its exit code. The `--` is required. Details and failure behaviour:
[Concepts](concepts.md#delivery-direct-and-adapter). Any fault prints
`polymorph adapter: this tool call was NOT checked by the guard hook (...)` and does not
deny.

## `validate`

```
polymorph validate FILE.toml
```

Checks one mapping file against schema 1 and prints `FILE: ok` (exit 0), or one
`error: ...` line per problem on stderr (exit 1). It checks the file alone: it does not
compare `name` to the filename or check that `generate` can use the `format`/`template`.

## `conformance`

```
polymorph conformance [--harness NAME]
```

Replays `fixtures/NAME/*.json` (all harnesses without `--harness`) through the adapter's
translations. Prints `FAIL: ...` lines on stderr and `N passed, M failed`; exit 1 if any
failed. See [fixtures](writing-a-mapping.md#fixtures).

## `discover`

Capture what an unmapped harness sends to hooks. Walkthrough: [Discovery](discovery.md).

### `discover init`

```
polymorph discover init --harness NAME --config-path P [--event-name E] [--matcher M]
                        [--shape claude-settings|claude-like] [--log-dir DIR]
                        [--full] [--env-be-very-careful]
```

Writes the harness's config file `P` so that its one hook is a catch-all logger. Prints
`wrote P`.

| Flag | Meaning |
|---|---|
| `--harness NAME` | Name for the log (`NAME.jsonl`); must match `[a-z][a-z0-9_-]*`. |
| `--config-path P` | The config file to write or merge into. |
| `--event-name E` | Native event to hook. Default `PreToolUse`. |
| `--matcher M` | Native tool matcher. Default `*`. |
| `--shape` | `claude-settings` (default, `hooks.<Event>`) or `claude-like` (`hooks.enabled` + `hooks.events`, ZCode's). |
| `--log-dir DIR` | Absolute directory for the log. Default `./.polymorph-discovery` under the current directory. |
| `--full` | Log raw payloads. See below. |
| `--env-be-very-careful` | Log environment variable names. See below. |

If `P` exists it must be a JSON object; other keys are kept. **The event you name has its
hook list replaced** (`claude-settings`) or **the whole `hooks` table is replaced**
(`claude-like`). Run `init` on a scratch copy or a file you can restore, not on a config
with hooks you want to keep. The hook command embeds the absolute path of the Python
that ran `init` and the absolute log path, so it works from any directory.

### `discover log`

```
polymorph discover log [--full] [--env-be-very-careful] --out FILE
```

The logger hook itself: reads one payload on stdin and appends one JSON line to FILE. It
**always exits 0 and prints nothing**, even on a bad command line, unwritable log or
non-JSON input, so it can never block the harness. Failures go to `NAME.errors.jsonl`
beside the log when that folder is writable.

### `discover summary`

```
polymorph discover summary FILE.jsonl
```

Prints payload count, non-object lines and session count, then per-event top-level keys and
per-tool `tool_input` keys, each with counts. Names and counts only.

### `discover draft`

```
polymorph discover draft FILE.jsonl --name NAME
```

Prints a schema 1 mapping to stdout (redirect it to a file). It has no `verified` field,
all capabilities `false`, and comments with evidence counts. See
[Discovery](discovery.md#from-log-to-mapping).

### `discover reload-check`

```
polymorph discover reload-check --config-path P --scratch-log F [--finish]
                                [--event-name E] [--matcher M] [--shape claude-settings|claude-like]
```

Tests whether a running harness re-reads its hook config without a restart.

1. Without `--finish`: adds a temporary extra hook (logging to F) and prints what to do.
   Do not restart the harness. Have the agent make one tool call in the already-running
   session. A second start is refused (exit 2) until `--finish` runs.
2. With `--finish`: reports `RELOADED` (exit 0) if F got a line or `NOT SEEN` (exit 1)
   if it did not, and removes the hook. The file is restored byte-for-byte; if it changed
   in the meantime the hook is removed by marker instead. State is kept in
   `F.reload-state.json`.

`--shape` defaults to what the existing file looks like.

## The redaction default and `--env-be-very-careful`

Discovery logs are **redacted by default**. Keys are kept. Only structural values
survive verbatim: event name, tool name, session and tool-use ids, permission mode, effort,
`source`, `duration_ms`. Every other value (`tool_input`, `tool_response`, `cwd`,
`transcript_path`, prompts) becomes `{"type": ..., "length": N}` with no hash.

`--full` (on `init`, which bakes it into the hook command, or on `log`) records raw
payloads, including commands, file paths and file contents. A `--full` log must not be
shared, attached to an issue or committed.

`--env-be-very-careful` makes the logger look at its process environment and record the
**names, never the values,** of variables whose names contain `PROJECT`, `WORKSPACE` or
`HARNESS` (case-insensitive), in the line's `env_keys`. Without the flag the environment
is not read at all. The flag is spelled out because even names can say more than you
intend (a variable called `HARNESS_ACME_CUSTOMER` tells the reader something), and because
it is visible in the hook command `init` writes into the config. `init` accepts it to
bake it in; `log` accepts it directly.
