# polymorph

Reads a repo's Claude Code agent config (`.agents/settings.json`, `.agents/hooks/`)
and writes hook configs for other coding harnesses from declarative TOML mappings.
Python 3.11+, stdlib only. Contract: `docs/SPEC.md`.

```
python -m polymorph generate [--repo .] [--harness NAME ...] [--check]
python -m polymorph adapter --harness NAME -- SCRIPT
python -m polymorph validate FILE.toml
python -m polymorph conformance [--harness NAME]
python -m polymorph discover init --harness NAME --config-path P [--event-name E] [--matcher M] [--full]
python -m polymorph discover log [--full] --out FILE   # the logger hook; silent, always exit 0
python -m polymorph discover summary FILE.jsonl
python -m polymorph discover draft FILE.jsonl --name NAME
python -m polymorph discover reload-check --config-path P --scratch-log F [--finish]
```

Targets default to `enabled = ["zcode", ...]` in `.agents/harnesses.toml`.
`generate` prints, per target, settings with no cross-harness meaning (unmapped), hooks
the target cannot run (missing event or tool class), and a warning when the mapping has
no `verified` field. `--check` writes nothing and exits 1 when a generated file differs.

Mappings are looked up in the repo's `.agents/harnesses/NAME.toml` (overrides the
built-in `mappings/NAME.toml`, with a warning), then the built-in ones. A mappings
directory elsewhere needs `--trust-dir`. Hook scripts must resolve under
`.agents/hooks/`. Other keys in an existing target config file are preserved; only
`hooks` is rewritten.

Discovery mode, for a harness with no mapping: `discover init` writes (merging into any
existing JSON) a config whose only hook logs every payload to
`.polymorph-discovery/NAME.jsonl` (gitignored; env var names only, never values). Run a
session, then `discover draft` prints an unverified schema-1 mapping from what was seen:
tool classes by name heuristics (listed in `discover.py`), only observed payload paths,
no command text or paths from the log, nothing unknown guessed. Draft comments carry
per-field evidence counts (`# 9/9 Bash`), mark name-heuristic classes `# guessed from name`,
list tools left unclassified with the reason, and infer `config_path`/`template` only from
log evidence (a `cwd` plus Claude-style `hook_event_name` implies `.claude/settings.json`
and `claude-settings`), saying so in a comment.

**Privacy: logs are redacted by default.** Keys are kept; only structural values (event, tool
name, ids, permission mode) survive verbatim, and everything else (tool_input, tool_response,
`cwd`, `transcript_path`, prompts) becomes `{type, length}`, with no hash. `--full` (on `init` or
`log`) logs raw payloads including commands, paths and file contents; such a log must not be
shared, attached to an issue or committed. Env var names are always names only.

Each log line has top-level `event`, `tool_name` and `session_id` (null when absent) next to
`ts` and `keys`. `discover summary` tabulates per-event top-level keys, per-tool tool_input
keys, counts and session count; it prints names and counts only. Logger failures (non-JSON
or non-object stdin, unwritable log) are written to `<name>.errors.jsonl` beside the log when
that directory is writable; the logger still exits 0 with no stdout.

`discover reload-check` tests whether a running harness re-reads hook edits: it adds a
temporary extra hook (own scratch log) through the same merge, prints what to do, and
`--finish` reports whether the scratch log got a line and removes the hook, restoring the
file byte-for-byte (or stripping by marker if the file changed meanwhile). State lives in
`<scratch>.reload-state.json`; a second start is refused until `--finish` runs.

Not done: capability/timing notes beyond what payloads show (feedback 8), and feedback items
10 and 12. Not run against a live harness by this repo's tests.

Tests: `python -m unittest discover -s tests`
