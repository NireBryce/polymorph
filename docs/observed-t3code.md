# Observed: t3code (2026-10-30, live session, discovery logger)

t3code runs Claude Code through the Claude Agent SDK. Its hook payloads match
Claude Code's, so it needs no mapping of its own: the `claude` mapping applies.
Source: a live scratch session with `polymorph discover`; 30+ logged payloads.

Observed:
- Hooks fire for every tool call; project `.claude/settings.json` is honoured.
- Config reloads mid-session: a hook added to settings.json fired on the next
  tool call without a restart.
- `SessionStart` keys: cwd, hook_event_name, session_id, source, transcript_path.
- `PreToolUse` keys: cwd, effort, hook_event_name, permission_mode, prompt_id,
  session_id, tool_input, tool_name, tool_use_id, transcript_path.
- `PostToolUse`: the same plus duration_ms, tool_response.
- Tool names seen: Bash, Write, Read, ToolSearch, Monitor.
- Background `Bash` and `Monitor` fire PreToolUse and PostToolUse at launch;
  PostToolUse fires at launch, not at completion. Task completion events fired no
  hook in the per-event listing (not checked line by line).
- `Monitor` does fire hooks, which settles that open question for this harness.

Not observed: exit-2 blocking, `ask` enforcement, input rewriting, resume behaviour.
The draft mapping's capability flags were all-false placeholders, not measurements.
Caveat for the guards: a PostToolUse guard on a background command sees it at launch.
