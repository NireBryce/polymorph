# Contributing

Thanks for looking. polymorph is **experimental and unvetted** (see the [README](README.md)
and [Limits](docs/limits.md)): interfaces and the mapping schema can change without notice,
and each change says so in the [CHANGELOG](CHANGELOG.md). Reports of problems are welcome
and are the most useful contribution right now.

## Reporting a harness

The most wanted report is "here is how harness X's hooks actually behave". Open an issue
with the **Harness report** form. What to include, and how to capture it with the discovery
logger without leaking your files, is in [Discovery](docs/discovery.md#what-to-send). In
short:

- say how you collected it (self-report, logger capture, or checked live by a person),
- redact home directories, usernames, repo and host names and tokens, keeping field names,
- never attach a `--full` discovery log or an unredacted payload,
- say what you did not test; a capture shows what was sent, not whether a deny worked,
- leave `verified` out of any draft mapping.

Please do not put in a report how a guard could be bypassed. Say what the harness does.

## Reporting a bug

Open an issue with: the polymorph commit, Python version, the command you ran, the full
output, and a minimal `.agents/settings.json` and mapping that reproduce it. Use generic
names (`mytool`, `acme`) rather than your real repo or host names.

## Adding a mapping

Prerequisite: you know how the harness behaves, from a capture or a live session. If not,
[draft one](docs/discovery.md#4-draft-a-mapping) and report it instead of merging a guess.

1. Write `src/polymorph/mappings/NAME.toml`. Field-by-field guide: [Writing a mapping](docs/writing-a-mapping.md).
   `name` equals the filename. Leave `verified` out unless a live session backs it, and
   if you add it, say exactly what was checked, how and when. Put the basis, and what is
   not covered, in a header comment as `src/polymorph/mappings/zcode.toml` does.
2. `python -m polymorph validate src/polymorph/mappings/NAME.toml` until it prints `ok`.
3. Add fixtures `src/polymorph/fixtures/NAME/{deny,ask,warn,remind}.json`
   ([format](docs/writing-a-mapping.md#fixtures)). Prefer ones built from a real capture.
4. `python -m polymorph conformance --harness NAME` passes.
5. If the harness's config file is not shaped like `claude-settings` or `claude-like`,
   that needs a new template in `generate.py`, which is a code change and needs tests.
6. Add a test where behaviour is not already covered, and a CHANGELOG line.

A mapping is data: no field may need to be executed. If a harness seems to need that,
open an issue to discuss it first.

## Changing code or docs

Run the tests before you send anything:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```

They use only the standard library, as does the code (Python 3.11+, `tomllib`, `json`,
`argparse`); please do not add a dependency.

- Write the failing test first when fixing a bug.
- The [SPEC](docs/SPEC.md) is the contract. If your change alters it, change it in the same
  pull request and say so in the description. Do not restructure it as a drive-by.
- Do not weaken a guard, a validation or the trust rules (hook scripts under
  `.agents/hooks/`, mappings as data, adapter faults never a deny) without discussion.
- Commands in the docs should be commands you ran. If you did not run one, mark it "not
  run".
- Examples use generic names (`mytool`, `acme`), never real machines, people or paths.
- Keep documentation to what the code does today; things that are planned are marked as
  planned.

Pull requests: describe what changed, what you ran, and which rung you reached
(evaluated by tests, ran against a hand-fed payload, ran in a live harness). Be specific
about the last one; it is the one that is rare here.

## Untrusted input

Issues, harness reports, draft mappings and discovery logs come from other people or from
agents, and some are written by an LLM. Treat all of them as data, never as instructions:
do not run a command because a report suggests it, do not paste a draft mapping's contents
into a shell, and do not execute a captured payload. `discover draft` already copies back
only strictly pattern-matched names; read its output before you commit it anyway. The same
goes for text inside a payload or log (it can say anything, including "ignore previous
instructions").

## Conduct

Be direct and kind, assume good faith, and keep reports to what the software does.
Harassment and personal attacks are not welcome; maintainers may edit or remove comments
and close issues that are.

## License

By contributing you agree your contribution is licensed under Apache-2.0, the project's
license ([LICENSE](LICENSE)).
