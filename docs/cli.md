# CLI reference

Run as `python -m polymorph ...` or, after an install, `polymorph ...`. Exit codes: `0`
success; `1` the command ran and found a problem (drift, findings, failed fixtures, an
invalid mapping); `2` usage or internal error (`polymorph: error: ...` on stderr);
`3` (`generate` only) a hook needs something the target lacks and no decision answers it. The
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
- `question:` an undecided mismatch (see below); the target is then `NOT WRITTEN`.
- `decided:` / `weakened:` a recorded decision that was applied; `weakened` for `run-empty` and for a `substitute` that moves the hook to a different event.
- `warning: stale decision` a decision whose id matches nothing now.
- `note:` harness payload fields no hook reads. Never a question.
- `unmapped setting:` a setting with no cross-harness meaning.

**Decisions.** When a target lacks an event (`no-event`), a tool class (`no-tool-class`) or a
payload field (`missing-field`) that a hook needs, `generate` writes nothing for that target,
prints one `question:` block per item (id, hook, problem, options, default `skip`) and exits
`3` after handling the other targets. Answer in `.agents/polymorph/decisions/NAME.toml` and
run again; it never prompts. `--check` also exits 3 for undecided items. Format and choices:
[SPEC, Decisions](SPEC.md#decisions).

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
| `decision-undecided` | error | The target lacks an event, tool class or field a hook needs and no decision answers it (the same item `generate` asks about, exit 3). |
| `decision-weakens-guard` | warning | An applied decision is `run-empty`, or a `substitute` onto a different event. |
| `decision-stale` | warning | A decision's id matches nothing now. |
| `decision-invalid` | error | The decision file or one entry fails validation. |
| `setting-unmapped` | note | A setting was not translated. |
| `generated-drift` | error | A generated file differs from `generate`'s output, or is missing. |
| `fixtures-missing` | warning | A built-in mapping has no fixtures. |
| `fixtures-skipped` | note | A repo-local mapping: fixtures are not checked. |
| `fixture-failed` | error | A fixture did not replay. |

If a hook command is rejected, checks derived from the config (decisions, unmapped, drift) are
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

Replays `src/polymorph/fixtures/NAME/*.json` (all harnesses without `--harness`) through the adapter's
translations. Prints `FAIL: ...` lines on stderr and `N passed, M failed`; exit 1 if any
failed. See [fixtures](writing-a-mapping.md#fixtures).

## `capture`

**Cost:** this runs a real AI model on your account and spends tokens (money or plan usage). Read [Cost and responsibility](../README.md#cost-and-responsibility) before running it.

Run the [discovery](discovery.md) procedure against a harness by itself.

```
polymorph capture --harness NAME [--yes] [--keep] [--steps STEP ...] [--step-timeout S]
                  [--total-timeout S] [--out DIR] [--full]
polymorph capture --print-prompts
```

It makes a scratch directory as `<system temp>/polymorph-capture-NAME-XXXXXXXX` (a fresh mode-0700 directory), installs the
discovery logger there through a driver, runs each step as a headless harness invocation with
that directory as the working directory, then removes the logger and (unless `--keep`) the
directory, also on an exception, a timeout, Ctrl-C (exit 130), SIGTERM (143) or SIGHUP (129). Drivers are code shipped with
polymorph and chosen by name; an unknown name exits 2, lists the known drivers and points at
`--print-prompts`.

| Driver | Command per step | Status |
|---|---|---|
| `opencode` | `opencode run --standalone --auto PROMPT`, logger plugin in `.opencode/plugins/polymorph-discovery/index.js` (from [the example plugin](examples/opencode-discovery-plugin.js)) | Flags from `opencode run --help` (v2.0.32); not run by the tests or by the author's session |
| `claude` | `claude -p PROMPT --permission-mode bypassPermissions --setting-sources project`, hooks in `.claude/settings.json` | **Blocked**: `capture --harness claude` refuses to run (exit 2). It has never run against a real `claude`, it needs `--permission-mode bypassPermissions`, and whether hooks fire under `-p` is unconfirmed, so a run could spend tokens and log nothing. Use `--print-prompts`. It is unblocked by removing `blocked` in `capture_drivers.py` once a person has confirmed it live. |

Steps (fixed constants, in this order; `--steps` selects a subset): `shell` (`echo hello`),
`file` (write, read and delete `scratch.txt`), `search`, `background` (`sleep 2; echo done`).
Nothing read from a log, a mapping or a harness reply is used as a prompt or a command.

| Flag | Meaning |
|---|---|
| `--harness NAME` | Driver name. Required unless `--print-prompts`. |
| `--yes` | Skip the question. The cost warning is still printed, as a short notice. With a non-interactive stdin and no `--yes`, the command refuses (exit 2). Pass it only knowing [what it costs](../README.md#cost-and-responsibility). |
| `--keep` | Keep the scratch directory after a run and print its path. Answering no at the question keeps nothing. |
| `--steps STEP ...` | Run only these steps. |
| `--step-timeout S` / `--total-timeout S` | Per step (default 120) and overall (default 600) limits, in seconds; each must be finite and above 0 (else exit 2). A timed-out step's process group is killed. |
| `--out DIR` | Results directory; default `./polymorph-capture-NAME`; must be absent or empty, and creatable and writable: checked before the first step (else exit 2). |
| `--full` | Log raw payloads (commands, paths, contents). Default is redacted. The results then hold a raw log: do not share it. |
| `--print-prompts` | Print the [prompt kit](discovery.md#prompt-kit-for-a-harness-capture-has-no-driver-for) and exit 0, running nothing. |

Confirmation: a framed warning says it runs a real model once per step, spends tokens, may
cost money or count against plan limits, that polymorph cannot tell which or measure it, and
that tool calls are auto-approved in the scratch directory. It shows the step count and the
timeouts, not a price. The question is `Continue? Type "yes" to run, anything else cancels [no]:`;
only `yes` (any case) proceeds, anything else exits 0 with a note that nothing ran.

The harness runs with `PATH`, `HOME` and `XDG_*` only; the environment is never logged and the
harness's authentication configuration is never read. Its stdout and stderr are discarded.

Output in the results directory: `summary.txt` (`discover summary`), `draft.toml`
(`discover draft`, unverified), `report.md` (polymorph version, `<harness> --version`, a row per
step with its exit status and log lines, the tool calls from the log with counts and input key
names, and the steps that produced no log line) and `log.jsonl`. Exit 0 if every step exited 0,
1 if one failed, timed out or was skipped, 2 for a usage error.

## `discover`

Capture what an unmapped harness sends to hooks. Walkthrough: [Discovery](discovery.md).

### `discover init`

```
polymorph discover init --harness NAME --config-path P [--event-name E] [--matcher M]
                        [--shape claude-settings|claude-like] [--log-dir DIR]
                        [--full] [--env-be-very-careful]
```

Adds a catch-all logger hook to the harness's config file `P`, as its own matcher group beside
any hooks already there. Prints `wrote P`.

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

If `P` exists it must be a JSON object; every other key, event and matcher group is kept
(for `claude-like`, other keys under `hooks` too, though `hooks.enabled` is set true so the
logger runs). Running `init` again with the same harness and log directory replaces its own
logger entry (recognised by its `--out` log path) instead of adding a second. If `hooks`, or
the part of it `init` has to extend, cannot be parsed, `init` exits 2 with a "refusing to
touch the file" message and writes nothing. The file is not backed up. The hook command embeds the absolute path of the Python
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
polymorph discover summary FILE.jsonl [--include EVENT ...] [--event EVENT ...]
```

Prints payload count, non-object lines and session count, the log-line schema marks, then
per-event top-level keys and per-tool `tool_input` keys, each with counts. Names and counts only.

By default only **tool events** (lines with a tool name) are used, so a flood of session
events from a server-wide bus does not drown them; the hidden event names and counts are
printed on a `hidden:` line. `--include EVENT` (repeatable) adds an event to the default;
`--event EVENT` (repeatable) replaces the default with exactly those events. Lines are
sorted by `ts` before counting, because each hook event is logged by its own process and
lines can land out of order. When lines carry a call id and a message id (`tool_use_id` or
`id` / `call_id`, and `messageID` / `message_id`), `before` and `after` lines are paired by
them and the `pairing` line reports paired, out-of-order, unpaired and id-less counts.

### `discover draft`

```
polymorph discover draft FILE.jsonl --name NAME [--include EVENT ...] [--event EVENT ...]
```

Prints a schema 1 mapping to stdout (redirect it to a file). It has no `verified` field,
all capabilities `false`, and comments with evidence counts. `--include` and `--event`
select lines as for `summary` (tool events by default). Event names may contain `.`, `-` and
`:`; a name ending in `before` or `after` is guessed as `pre_tool` or `post_tool` and marked
`guessed from name`, and every other observed event name is listed in a comment under
`[events]`. Boolean `tool_input` keys (for example a shell `background` flag) and the key
paths of output-like objects are listed in comments. See
[Discovery](discovery.md#4-draft-a-mapping).

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

Two kinds of value are kept for structure. Call and message ids (`id`, `tool_use_id`,
`call_id`, `messageID` and similar) stay verbatim, but only when they look like ids
(letters, digits, `_ . : -`, at most 128 characters); anything else under those names is
reduced like any other value. Output-like objects (`tool_response`, `output`, `result`)
keep their nested key names to three levels and the key count is capped, with each leaf
reduced to `{type, length}`; a key name that is not a plain identifier is replaced by
`<invalid key>`. Each log line also carries `schema: 1`; older lines without it still read.

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
