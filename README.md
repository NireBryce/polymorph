# polymorph

Reads a repo's Claude Code agent config (`.agents/settings.json`, `.agents/hooks/`)
and writes hook configs for other coding harnesses from declarative TOML mappings.
Python 3.11+, stdlib only. Contract: `docs/SPEC.md`.

```
python -m polymorph generate [--repo .] [--harness NAME ...] [--check]
python -m polymorph check [--repo .] [--harness NAME ...] [--format text|json|sarif] [--strict]
python -m polymorph adapter --harness NAME -- SCRIPT
python -m polymorph validate FILE.toml
python -m polymorph conformance [--harness NAME]
python -m polymorph discover init --harness NAME --config-path P [--event-name E] [--matcher M] [--full]
python -m polymorph discover log [--full] --out FILE   # the logger hook; silent, always exit 0
python -m polymorph discover summary FILE.jsonl
python -m polymorph discover draft FILE.jsonl --name NAME
python -m polymorph discover reload-check --config-path P --scratch-log F [--finish]
```

Targets default to `enabled = ["zcode", ...]` in `.agents/polymorph/config.toml`.
`generate` prints, per target, settings with no cross-harness meaning (unmapped), hooks
the target cannot run (missing event or tool class), and a warning when the mapping has
no `verified` field. `--check` writes nothing and exits 1 when a generated file differs.

Mappings are looked up in the repo's `.agents/polymorph/mappings/NAME.toml` (overrides the
built-in `mappings/NAME.toml`, with a warning), then the built-in ones. A mappings
directory elsewhere needs `--trust-dir`. Hook scripts must resolve under
`.agents/hooks/`. Other keys in an existing target config file are preserved; only
`hooks` is rewritten.

Discovery mode, for a harness with no mapping: `discover init` writes (merging into any
existing JSON) a config whose only hook logs every payload to
`.polymorph-discovery/NAME.jsonl` (gitignored; redacted by default). Run a
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
shared, attached to an issue or committed. Environment variables are not read unless `--env-be-very-careful` is passed; then only the names (never values) of variables containing PROJECT, WORKSPACE or HARNESS are logged.

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

## `polymorph check`

Static analysis of a repo's agent config, for CI and pre-commit. It never runs a hook
script, a harness or the network, and never writes a file. Findings are
`{severity, code, target, message, file}`; severities are error, warning, note. Exit 0 when
there are no errors (with `--strict`, no warnings either), 1 when findings fail, 2 on usage
or internal errors. `--format sarif` emits SARIF 2.1.0 for code-scanning upload.

Codes: `no-targets`, `settings-invalid`, `mapping-invalid`, `mapping-unverified`,
`mapping-untested-live`, `hook-outside-hooks-dir`, `hook-missing`, `hook-not-executable`,
`hook-no-equivalent`, `setting-unmapped`, `generated-drift`, `fixtures-missing`,
`fixtures-skipped`, `fixture-failed`. Contract: `docs/SPEC.md`.

pre-commit (`.pre-commit-config.yaml`):

```yaml
repos:
  - repo: local
    hooks:
      - id: polymorph-check
        name: polymorph check
        entry: python -m polymorph check --strict
        language: system
        pass_filenames: false
        files: ^\.(agents|zcode|claude)/
```

GitHub Actions:

```yaml
name: polymorph
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    permissions: {contents: read, security-events: write}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install .   # or however polymorph is installed in your repo
      - run: python -m polymorph check --format sarif > polymorph.sarif
        continue-on-error: true
      - uses: github/codeql-action/upload-sarif@v3
        with: {sarif_file: polymorph.sarif}
      - run: python -m polymorph check --strict
```

Tests: `python -m unittest discover -s tests`

## License

Apache-2.0; see [LICENSE](LICENSE).
