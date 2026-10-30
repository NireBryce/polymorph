# Using polymorph in CI

`polymorph check` is built for this: it is static, writes nothing, needs no network, and
exits non-zero on errors. What it catches is drift and inconsistency (a settings edit with
no regenerated file, a hook script that lost its execute bit, a hook the target cannot run).
It does not show that the guards work; see [Limits](limits.md).

None of the snippets on this page have been run in a real CI system or with pre-commit
installed ("not run"). The commands inside them were run locally.

## Installing it in CI

The built-in mappings and fixtures ship inside the Python package, so a plain
`pip install .` from a clone works as well as an editable install.

```sh
git clone https://github.com/NireBryce/polymorph /tmp/polymorph     # not run
pip install /tmp/polymorph                                          # not run (a local editable install was run; a plain install into a fresh venv was run)
```

Pin a commit (`git -C /tmp/polymorph checkout <sha>`) so a change in polymorph cannot
change your CI result. polymorph is experimental and its interfaces may change.

## Which flags

- `polymorph check` fails the job on errors only.
- `polymorph check --strict` fails on warnings too: an unverified mapping
  (`mapping-unverified`), a hook the target cannot run (`hook-no-equivalent`), missing
  fixtures. With the shipped `gemini` draft enabled, `--strict` always fails; that is the
  point of the warning.
- Notes (`mapping-untested-live`, `setting-unmapped`) never fail a run.
- Use `--harness NAME` to check one target regardless of `config.toml`.

## pre-commit

Not run.

```yaml
# .pre-commit-config.yaml
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

Adjust `files` to the config folders of your enabled harnesses (the mappings' `config_path`
directories plus `.agents/`).

## GitHub Actions

Not run. Uploads the findings as code-scanning alerts, then gates on `--strict`.

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
      - run: git clone https://github.com/NireBryce/polymorph "$RUNNER_TEMP/polymorph"
      - run: pip install -e "$RUNNER_TEMP/polymorph"
      - run: python -m polymorph check --format sarif > polymorph.sarif
        continue-on-error: true
      - uses: github/codeql-action/upload-sarif@v3
        with: {sarif_file: polymorph.sarif}
      - run: python -m polymorph check --strict
```

Notes:

- The SARIF step uses `continue-on-error` so the upload still happens when `check` finds
  errors; the last step is what fails the job.
- Uploading SARIF needs `security-events: write` and, for private repositories, GitHub
  code scanning to be available for the repo.
- SARIF levels are `error`, `warning` and `note`, with `ruleId` set to the finding code
  (`generated-drift` and so on). A finding that has a file gets a location; findings about
  a target as a whole have none.
- `--format json` gives `{findings: [...], summary: {error, warning, note}}` if you want to
  post-process. The SARIF and JSON formats were produced locally.

## Keeping generated files in sync

Commit the generated files (`.zcode/config.json` and so on). CI then catches the case where
`.agents/settings.json` changed and nobody re-ran `generate`: `check` reports
`generated-drift` and names the file. The fix is `python -m polymorph generate` and a commit.
`python -m polymorph generate --check` does the drift test alone, with exit 1, if you want a
smaller job.
