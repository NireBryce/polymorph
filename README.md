# polymorph

Reads a repo's Claude Code agent config (`.agents/settings.json`, `.agents/hooks/`)
and writes hook configs for other coding harnesses from declarative TOML mappings.
Python 3.11+, stdlib only. Contract: `docs/SPEC.md`.

```
python -m polymorph generate [--repo .] [--harness NAME ...] [--check]
python -m polymorph adapter --harness NAME -- SCRIPT
python -m polymorph validate FILE.toml
python -m polymorph conformance [--harness NAME]
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

Tests: `python -m unittest discover -s tests`
