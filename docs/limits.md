# Limits

polymorph is experimental, unvetted and sits on a safety path. This page lists what is not
established, in plain terms, so you can decide how much to lean on it. Read it before you
trust a generated hook config.

## What `check` does and does not tell you

A clean `polymorph check` means: the hook scripts named in your settings exist, are
executable and live under `.agents/hooks/`; each mapping is valid; the generated files on
disk match what `generate` would write; the shipped fixtures replay.

It does **not** mean:

- the harness reads the generated file, or reads it when you expect,
- the harness calls the hook for the tools you think it does,
- a deny from the hook stops the call,
- an `ask` prompts the user,
- the hook script itself is correct,
- the mapping's tool names and payload paths are right for the version you run.

`check` raises a `mapping-untested-live` note on every run to remind you of this. It never
fails the build, by design: it is a reminder, not a result.

## What "verified" means

The `verified` field of a mapping is free text. polymorph checks only that it is present;
it does not read it, and nothing stops anyone writing it for a mapping that was not tested.
The convention this project follows:

- **Absent** means unverified. Drafts, the `gemini` mapping, and anything written from an
  issue report start here. `generate` warns and `check` raises `mapping-unverified`.
- **Present** means a named person looked at a named source on a named date, and the text
  says what and how: for example, "payload shape from a captured session on DATE" or "read
  from hook-script comments, not a live run". The text should claim only what was checked.
- **Verified for real use** needs a live session: the generated config loaded in the actual
  harness, a hook seen to fire, and a deny seen to stop a tool call. Capture of payloads
  alone is not that.

Things a live test needs to settle for a mapping, because payloads cannot show them: does
exit 2 block; does a JSON decision field block, and what is its name; is `ask` enforced or
treated as allow; does a post-tool event exist and fire; can a hook rewrite input; do
background tools fire hooks; does a changed hook file need a restart
(`discover reload-check` helps with the last). Until then the capability flags are claims,
and the safe default is `false`.

### Where the shipped mappings stand

| Mapping | `verified` | Basis |
|---|---|---|
| `claude` | present | Hook files running under Claude Code with these field names, plus a captured session via t3code (see [observed-t3code.md](observed-t3code.md)). Block semantics not live-tested. |
| `zcode` | present | Comments in hook scripts that already run under ZCode (payload shape, `ask` treated as allow). Not a live run of polymorph's output. `exit_2_blocks`, `post_event`, `rewrites_input`, `skills_dir` and `context_file` are best guesses. |
| `gemini` | absent | A draft from an issue sketch. Every name is a guess. No fixtures. |

Read the mapping's own header comment before relying on it; it states what is and is not
covered.

## Known gaps

**Behaviour**

- The adapter has run in unit tests and a hand-fed run, never as the command of a real
  harness. Discovery has not been run against a live harness by this repository's tests.
- **The adapter fails open, visibly.** A malformed payload, a hook that crashes or prints
  non-JSON, a 60 second timeout or an internal fault lets the tool call proceed and prints
  `polymorph adapter: this tool call was NOT checked by the guard hook (...)`. That is
  deliberate (never invent a deny), but it means a broken or missing polymorph install
  silently removes the guard from the model's point of view unless the harness surfaces the
  message. For an adapter-delivered hook, a failing `python -m polymorph` (not on `PATH`,
  not importable) is the harness's problem to report, and the harness may treat it as
  non-blocking.
- The adapter reads the mapping as raw TOML without validating it. `check` and `validate`
  do; run them.
- `permissions` allow/deny rules, `attribution`, other top-level settings and hook events
  other than SessionStart, PreToolUse and PostToolUse (such as `Stop`) are reported as
  unmapped and not carried over. Another harness will not inherit your deny list.
- `mcp` and `other` tool classes: no shipped mapping names tools for them. A hook scoped
  only to them is skipped for those targets and reported as a gap.
- `generate` rewrites the whole `hooks` key of the target file; hooks added there by hand
  are lost.
- The adapter reads `payload_in` entries of the form `read.path`, `read.pattern` and
  `read.glob` but stores them under those names rather than in `tool_input`, so a hook
  that looks in `tool_input.path` will not see them. `shell.command`, `edit.file_path` and
  `read.file_path` are placed correctly. Direct delivery is unaffected (no translation).
- A mapping's `format` may validate as `toml` or `yaml`, but only `json` can be generated,
  and only with the `claude-settings` and `claude-like` templates.
- `context_file` and `skills_dir` are recorded and validated but nothing uses them yet.
- Hooks must be a single bare script path; commands with arguments are refused.

**Packaging**

- <a id="packaging"></a>Mappings and fixtures are found relative to the source tree. Use `pip install -e .` or
  the Nix package. A plain `pip install .` produces an install in which `conformance` and
  any built-in mapping fail with a missing-directory error.

**Not built**

- `.agents/polymorph/decisions/` and `generated/`, code-generating targets, plugin
  packaging, and capability or timing notes beyond what payloads show.
- Only a Claude Code reader exists. Canonical config comes from `.agents/settings.json`.

**Security posture**

- Guards in this ecosystem are usually string matchers. They are not a security boundary,
  and polymorph, which copies them to more places, does not make them one.
- polymorph's own protections (scripts confined to `.agents/hooks/`, mappings as data,
  strict name patterns when drafting) limit what a mapping file or log can make polymorph
  do. They do not review your hook scripts.
- Nobody outside the author has reviewed the code.

## Where the code and the spec differ

[SPEC.md](SPEC.md) is the contract. Where the code does something it does not say:

- The spec says discovery logs are "gitignored". Only polymorph's own repository ignores
  `.polymorph-discovery/`; `discover init` does not edit your `.gitignore`.
- The spec's CLI summary omits some flags that exist: `generate --mappings --trust-dir`,
  `adapter --mappings --trust-dir`, `discover init --shape --log-dir --env-be-very-careful`,
  `discover log --env-be-very-careful`, and `discover reload-check --event-name --matcher
  --shape`. [CLI reference](cli.md) lists them.
- The spec's mapping example lists `payload_in`, `payload_out`, `[tools]`, `[events]` and
  all five capabilities as if optional; `validate` (and so `check`) requires all of them,
  although `generate` itself loads a mapping without them.
- The spec says fixtures cover deny, ask, warn and remind "for each mapping"; `gemini` has
  none.
- The spec's `read.path`, `read.pattern` and `read.glob` appear in the direct-delivery
  identity table as `tool_input.*`; the adapter does not place them there (above).
