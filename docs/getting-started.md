# Getting started

This walks through a repo that has a Claude Code style agent config and one hook, and
wires the same hook into ZCode. Every command below was run against a scratch repo with
this exact content unless marked "not run".

## What you need

- Python 3.11 or newer. No third-party packages.
- A clone of polymorph, installed (`pip install .`, or `-e` to work on it), or Nix.
- A repo with `.agents/settings.json` and hook scripts under `.agents/hooks/`.

In the examples `python -m polymorph` is shorthand for however you run it. From a clone
without installing, use `PYTHONPATH=/path/to/polymorph/src python3 -m polymorph`.

## 1. A repo to work with

```
.agents/
  settings.json
  hooks/block-rm.sh        executable
```

`.agents/settings.json`:

```json
{
  "permissions": { "allow": ["Bash(ls)"] },
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash",
        "hooks": [ { "type": "command",
                     "command": "\"$CLAUDE_PROJECT_DIR/.agents/hooks/block-rm.sh\"",
                     "timeout": 10 } ] }
    ]
  }
}
```

`.agents/hooks/block-rm.sh` (the hook reads the canonical payload on stdin and prints a
Claude-style answer to refuse):

```sh
#!/bin/sh
if grep -q 'rm -rf /' ; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"refusing rm -rf /"}}'
fi
```

Two rules about hook commands: each must be a bare path to one script (no arguments, no
`python script.py`), and the script must resolve under `.agents/hooks/`. Anything else is
an error, because arguments and interpreters do not carry across harnesses.

## 2. Choose targets

```sh
mkdir -p .agents/polymorph
printf 'enabled = ["zcode"]\n' > .agents/polymorph/config.toml
```

`enabled` is the list of harnesses `generate` and `check` act on when you do not pass
`--harness`. Each name must have a mapping: the shipped ones are `claude`, `zcode` and
`gemini` (gemini is an unverified draft, see [Limits](limits.md)).

## 3. Preview, then generate

```sh
python -m polymorph generate --check
```
```
[zcode] would write .zcode/config.json (DRIFT)
  unmapped setting: permissions.allow (1 entries)
```
Exit code 1: the file does not exist, so it differs. Nothing was written.

```sh
python -m polymorph generate
```
```
[zcode] wrote .zcode/config.json (DRIFT)
  unmapped setting: permissions.allow (1 entries)
```

The result, `.zcode/config.json`:

```json
{
  "hooks": {
    "enabled": true,
    "events": {
      "PreToolUse": [
        { "matcher": "Bash",
          "hooks": [ { "type": "command",
                       "command": "\"$CLAUDE_PROJECT_DIR/.agents/hooks/block-rm.sh\"",
                       "enabled": true,
                       "timeout": 10 } ] }
      ]
    }
  }
}
```

(Reformatted for width; the real file is `json.dumps(indent=2)`.) ZCode speaks the
canonical protocol, so the hook is called directly. A harness that does not gets a command
that goes through polymorph's adapter instead; see
[delivery](concepts.md#delivery-direct-and-adapter).

`generate` keeps other top-level keys already in the file and rewrites the whole `hooks`
key. Hand-written hooks in the generated file are replaced.

## 4. Check

```sh
python -m polymorph check
```
```
[zcode]
  note mapping-untested-live zcode: not tested live: block semantics, ask enforcement unproven
  note setting-unmapped zcode: permissions.allow (1 entries)
0 error(s), 0 warning(s), 2 note(s)
```

Exit 0. Before `generate` ran, the same command exits 1 with
`error generated-drift ... .zcode/config.json missing`. Edit `.agents/settings.json` later
and `check` reports drift again until you re-run `generate`.

The two notes are permanent. `mapping-untested-live` is polymorph reminding you that
nobody has proved ZCode enforces a deny from this file; `setting-unmapped` lists what was
not translated. `check --strict` makes warnings fail too (these are notes, so it still
passes).

## 5. Try the adapter by hand

For a harness with `delivery = "adapter"` (the default) the generated command is
`python -m polymorph adapter --harness NAME -- "$CLAUDE_PROJECT_DIR/<script>"`. You can feed
it a payload yourself. Using the unverified `gemini` mapping, whose shell tool is called
`run_shell_command`:

```sh
echo '{"tool_name":"run_shell_command","tool_input":{"command":"rm -rf /"}}' \
  | python -m polymorph adapter --harness gemini -- .agents/hooks/block-rm.sh
echo $?
```
```
refusing rm -rf /
2
```

The adapter turned the native payload into the canonical one, ran your script, and mapped
the deny to this harness's form (exit 2 with the reason on stderr). That is a hand-fed run,
not evidence that Gemini CLI behaves this way.

## Next

- [Concepts](concepts.md) for what the pieces are.
- [CI](ci.md) to keep generated files from drifting.
- [Writing a mapping](writing-a-mapping.md) or [Discovery](discovery.md) for a harness
  polymorph does not know.
