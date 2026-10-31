# Discovery: capturing a harness

Use discovery when a harness has hooks but polymorph has no mapping for it. You install a
logger as the harness's hook, run a short session, and polymorph drafts a mapping from
what the harness actually sent. The draft is unverified and says so.

Discovery needs a harness that runs command hooks from a JSON config file. If yours does
not, use the self-report route at the end instead.

Examples use a made-up harness `mytool` whose config is `.mytool/config.json`.

## 1. Install the logger

`init` adds the logger beside your existing hooks and does not back the file up (see
[CLI reference](cli.md#discover-init)); a scratch copy of the config is still the safe place.

```sh
python -m polymorph discover init --harness mytool --config-path .mytool/config.json
```
```
wrote .mytool/config.json
```

That file now has one catch-all hook, `PreToolUse` with matcher `*`, whose command is
`<python> -m polymorph discover log --out <abs path>/.polymorph-discovery/mytool.jsonl`.
Change the event or matcher with `--event-name` and `--matcher` if the harness uses
different names; use `--shape claude-like` if its file wraps hooks in
`hooks.enabled` / `hooks.events` as ZCode's does. To capture more than one event, run
`init` again with a different `--event-name`: other events are kept.

## 2. Run a session

Start the harness in the directory where you ran `init` and ask it to do a few ordinary
things. Aim for coverage, because the draft only contains what was seen:

- run a harmless shell command (`ls`, `echo hello`),
- have it create and edit a small file,
- have it read a file and search for something,
- if it has other tool types (MCP tools, background commands, a task or monitor tool),
  use each once,
- start a fresh session, if you also ran `init` for the session-start event.

A prompt you can paste:

> Please do these in order and stop after the last: run `echo hello` in the shell; create
> `scratch.txt` containing one line; change that line; read the file back; search the
> directory for the word "hello".

Only the events you ran `init` for are logged. Run `init` again with
`--event-name PostToolUse` (and the native session-start name) to see those payloads.

## 3. Look at what you got

```sh
python -m polymorph discover summary .polymorph-discovery/mytool.jsonl
```
```
payloads: 3   non-object lines: 0   sessions: 1

per event: top-level keys
  name                     count  keys (present/count)
  PreToolUse                   2  cwd 2/2, hook_event_name 2/2, session_id 2/2, tool_input 2/2, tool_name 2/2
  PostToolUse                  1  cwd 1/1, hook_event_name 1/1, session_id 1/1, tool_input 1/1, tool_name 1/1, tool_response 1/1

per tool: tool_input keys
  name                     count  keys (present/count)
  run_cmd                      2  command 2/2
  write_file                   1  content 1/1, file_path 1/1
```

Names and counts only. By default only tool events are counted: a harness with a
server-wide event bus can log thousands of session events per tool call, and the
`hidden:` line says what was left out (`--include EVENT` adds it back, `--event EVENT`
selects exactly one). Lines are sorted by `ts` first, and `before` / `after` lines are
paired by call id and message id when the payloads have them (see
[CLI reference](cli.md#discover-summary)). If a count is `0 payloads`, the hook did not fire: check the file
location, the event name, and whether the harness reads hook config at all.

If you suspect the harness needs a restart to see a changed hook file, test it:
`discover reload-check` ([CLI reference](cli.md#discover-reload-check)).

## 4. Draft a mapping

```sh
python -m polymorph discover draft .polymorph-discovery/mytool.jsonl --name mytool > mytool.toml
python -m polymorph validate mytool.toml
```

On the three-payload log above the draft is:

```toml
# DRAFT mapping for mytool, drafted from 3 observed payloads. UNVERIFIED.
...
schema = 1
name = "mytool"
config_path = ".claude/settings.json"   # inferred: log has cwd + hook_event_name (Claude-style); confirm
format = "json"
template = "claude-settings"   # inferred from the same evidence; confirm

[tools]
edit = ["write_file"]   # guessed from name (1 calls)
# Not classified (left out, not guessed):
#   run_cmd (2 calls): no name heuristic matched

[events]
pre_tool = "PreToolUse"   # 2/3 payloads
post_tool = "PostToolUse"   # 1/3 payloads

[capabilities]
# Not observable from payloads; all false (conservative) until checked live.
ask = false
...
[payload_in]
tool_name = "tool_name"   # 3/3 payloads
cwd = "cwd"   # 3/3 payloads
session_id = "session_id"   # 3/3 payloads
"edit.file_path" = "tool_input.file_path"   # 1/1 write_file
```

How to read it:

- It only contains what was observed. `shell` is missing because `run_cmd` matched no
  name heuristic, and the draft says so instead of guessing. You add `shell = "run_cmd"`
  after confirming.
- Tool classes come from name patterns in `discover.py` (shell: `bash`, `shell`,
  `terminal`, `command`, `exec`, `sh`; edit: `edit`, `write`, `patch`, `replace`,
  `create`; read: `read`, `grep`, `glob`, `search`, `find`, `list`, `view`, `cat`) and are
  marked `guessed from name`. MCP tools are never classified.
- `config_path` and `template` are inferred only when the log looks Claude-style (a `cwd`
  and a `hook_event_name`). Otherwise they are placeholders marked `unknown`.
- Counts are `present/observed`. `1/1 write_file` means one of the one `write_file` payloads
  had that field.
- Event names may contain `.`, `-` and `:` (OpenCode sends `execute.before`,
  `execute.after`). A name ending in `before` or `after` is guessed as `pre_tool` or
  `post_tool` and marked `guessed from name`; other observed names are listed in a comment
  under `[events]`, not dropped.
- Comments under `[payload_in]` list boolean `tool_input` flags (such as a shell
  `background`), and comments under `[payload_out]` list the key paths seen in output-like
  objects, with values hidden. Enum-valued keys cannot be told apart from other strings
  once values are redacted, so they are not listed.
- All capabilities are `false`, because payloads cannot tell you whether exit 2 blocks or
  `ask` is enforced. Find out by testing, then change them. See
  [Writing a mapping](writing-a-mapping.md).
- No `verified` field. Keep it that way until someone has run the mapping in a live
  harness, and then write what they checked.

The draft never contains command text, file paths or anything else from the payload
values: only key names and tool/event names that match strict name patterns. The log is
treated as untrusted data; nothing in it is evaluated.

## Privacy

Default logs are redacted: values become `{"type": "string", "length": 17}`, with no hash,
so a payload with a command or a path shows only its size. Event names, tool names, ids
and a few structural fields stay. The `cwd`, `transcript_path` and every prompt, command
and file content are reduced to type and length.

Rules:

- Leave `--full` off unless you need to see raw values to figure something out. A
  `--full` log contains commands, paths and file contents. Never share it, attach it to an
  issue or commit it.
- Leave `--env-be-very-careful` off unless you need to find out which variables the
  harness sets. It records names only, for variables containing `PROJECT`, `WORKSPACE` or
  `HARNESS`. See [CLI reference](cli.md#the-redaction-default-and---env-be-very-careful).
- Add `.polymorph-discovery/` to your `.gitignore`. polymorph does not do that for you.
  Check `git status` before `git add -A`.
- "Redacted" is not "anonymous". Tool names, key names and lengths can still identify what
  you were doing. Look at the log before sharing it.
- Delete the log when you are done.

## Prompt kit (for a harness `capture` has no driver for)

`polymorph capture --print-prompts` prints these. They are what a person pastes into an
unknown harness by hand, one at a time, reading each answer before sending the next.
Placeholders: `HARNESS` the harness name, `SCRATCH` an empty directory made for this,
`LOG` the log file inside it, `POLYMORPH` the command that runs polymorph
(`<python> -m polymorph`).

**Prompt 1: read its own documentation, read-only.**

> You are running in the directory SCRATCH. Do not change any file and do not run any
> command in this step. Read HARNESS's own documentation or installed source (not
> third-party write-ups) and answer, quoting the file or page for each point: (a) which
> hook or plugin events exist for tool calls, before and after; (b) where its hook or
> plugin configuration is read from and in what format; (c) how a hook receives the tool
> name and input; (d) how a hook blocks a tool call; (e) whether a changed hook file is
> picked up without a restart. Write "not found" for anything the documentation does not
> say. Do not guess.

**Prompt 2: write the logger in the scratch directory, show it, do not run it.**

> Still in SCRATCH, still changing nothing outside it. Write the smallest hook or plugin
> that, for every tool call, pipes the event as one JSON object to the command
> `POLYMORPH discover log --out LOG` on standard input and does nothing else: it never
> throws, never blocks or alters the call, never prints, and never writes the payload
> anywhere except that command. Put it where HARNESS loads it from for this directory
> only, not a global location. Show me the complete file and the path, and stop. Do not
> run anything yet.

Read the file before you answer. It must contain only what is described above: no network
access, no other file written, no reading of the environment. If it does more, say what to
remove.

**Prompt 3: run canned steps, then summary and draft.**

> Do these in order in SCRATCH and stop after the last: (1) run `echo hello` in the shell;
> (2) create `scratch.txt` with one line, read it back, then delete it; (3) search the
> directory for the word "hello"; (4) run `sleep 2; echo done` as a background command.
> Then run `POLYMORPH discover summary LOG` and `POLYMORPH discover draft LOG --name
> HARNESS`, and show both outputs unedited. Then list which of steps 1-4 produced no line
> in the log.

Guard rails, whoever does it:

- Do it in a scratch directory made for this, never in a repository you care about, and
  delete the directory and the logger afterwards.
- The logger is read by a person before it runs. Prompt 2 never runs it.
- The log stays redacted (no `--full`). Nothing from the log or from the harness's own
  account is pasted back into a prompt, a command or a mapping; the draft is hand-reviewed
  against prompt 1's quotations ([the new-mapping skill](../.agents/skills/new-mapping/SKILL.md)).
- The harness's reply is a lead, not evidence. The log is.
- Do not print or open the harness's authentication configuration.
- This spends model credits and may auto-approve tool calls; keep the prompts exactly as
  written.

## What to send

Open an issue from the **Harness report** form
(`.github/ISSUE_TEMPLATE/harness-report.yml`; it is the "Harness report" choice when you
click New issue on the polymorph repository). It asks for:

- how the report was collected (agent self-report, discovery logger capture, or checked by
  a person live),
- harness name, version and OS,
- hooks: whether they exist, event names, config file location and format, a link to the
  harness's hooks documentation,
- tool names for shell, edit and read, and whether background-command tools exist and fire
  hooks,
- one sample payload and answer, ideally for a denied command,
- block semantics: does exit 2 block, what is the JSON decision field, is `ask` enforced,
  is there a post-tool event, can a hook rewrite input,
- skills directory and context file,
- a draft mapping (without `verified`).

From a discovery capture, send: the `discover summary` output, the `discover draft`
output, and, if you want to include a sample payload, one that you have read through and
edited by hand. Do not send the log file. Replace home directories, usernames, repo and
host names, and anything token-shaped with placeholders (`/home/USER`, `REPO`,
`<redacted>`), keeping field names and structure unchanged, and read command text too.

Say what you did and did not test. A capture shows what the harness sent; it does not show
whether a deny worked. [Limits](limits.md#what-verified-means) explains what polymorph
treats as verified.

Please do not include notes about what could slip past a guard or how to get around one.
Say what the harness does; that is enough.

## Self-report by an agent

If you cannot run a logger, an agent inside the harness can fill in the form from what it
can see about itself. That route is cheaper and often wrong, so the report is marked
unverified. The instructions an agent follows are in
[`.agents/skills/report-harness/SKILL.md`](../.agents/skills/report-harness/SKILL.md);
they require the agent to show you the exact text and get a yes before filing.

An example of what a completed capture looks like for a real harness is
[observed-t3code.md](observed-t3code.md).

For a harness that has plugins instead of command hooks, see
[observed-opencode.md](observed-opencode.md): the logger is called from a plugin, shown in
[examples/opencode-discovery-plugin.js](examples/opencode-discovery-plugin.js). The plugin
logs every tool call on the server, including other sessions; read its header first.
