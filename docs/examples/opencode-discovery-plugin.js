// polymorph-discovery: an OpenCode V2 server plugin that pipes hook payloads to
// `polymorph discover log`. An example from a capture of OpenCode v2.0.32
// (docs/observed-opencode.md), not something polymorph installs for you.
//
// ADAPT BEFORE USE: every path below (POLYMORPH_CMD, POLYMORPH_ENV, the --out log file, the plugin
// directory) is an example under /tmp. Point them at your own polymorph and log file.
//
// WARNING: OpenCode's hooks and event bus are server-wide. This plugin logs EVERY tool
// call on the server, including other sessions and other projects, and a flood of
// `session.created` events from internal child sessions. The logger redacts values by
// default, but the log still shows what ran, where and how often. Remove the plugin
// (delete its directory) when the capture is done. Use `polymorph discover summary`,
// which shows tool events only by default.
//
// Install: save as `.opencode/plugins/polymorph-discovery/index.js` (a directory per
// plugin; bare .js files directly under plugins/ are skipped). OpenCode loads it without a
// restart and reloads it when the file changes.
//
// Rules enforced below: never throws, never blocks or alters the tool call, never
// writes to stdout/stderr, swallows every error. Payloads are only ever piped to the
// logger command (which redacts them); nothing else reads or writes them.
//
// The `id` and `messageID` fields (what `discover` pairs before/after lines by) are
// copied from the documented native payload; the capture this was written from did not
// log them, so those two lines have not been run live.
//
// The default export must be `{ id, setup }` (or `{ id, effect }`).

import { spawn } from "node:child_process"

const POLYMORPH_CMD = "/tmp/pm-opencode/bin/polymorph" // example path
const POLYMORPH_ARGS = [
  "discover",
  "log",
  "--out",
  "/tmp/pm-opencode/.polymorph-discovery/opencode.jsonl", // example path
]
// The logger's whole environment (the spawn is given nothing else). PYTHONPATH is only
// needed when polymorph runs from a source checkout rather than an install.
const POLYMORPH_ENV = { PYTHONPATH: "/tmp/pm-opencode/src" } // example path

function serialize(payload) {
  try {
    return JSON.stringify(payload) + "\n"
  } catch {
    // Extremely defensive: if a native arg cannot be serialized (circular or
    // non-JSON values), still emit the event envelope rather than dropping
    // the event or writing the payload anywhere else.
    return (
      JSON.stringify({
        hook_event_name: payload.hook_event_name,
        tool_name: payload.tool_name,
        session_id: payload.session_id,
        input: "<unserializable input omitted>",
        output: "<unserializable output omitted>",
      }) + "\n"
    )
  }
}

// Fire-and-forget: spawn the logger, write one JSON line to its stdin, detach.
// Child stdout/stderr are ignored; every failure path is swallowed so a hook
// can never throw, block, or surface an error.
function pipeToPolymorph(payload) {
  try {
    const child = spawn(POLYMORPH_CMD, POLYMORPH_ARGS, {
      stdio: ["pipe", "ignore", "ignore"],
      env: POLYMORPH_ENV, // minimal: only what the logger needs, not OpenCode's environment
    })
    child.on("error", () => {})
    const stdin = child.stdin
    stdin.on("error", () => {})
    stdin.end(serialize(payload))
    child.unref()
  } catch {
    // swallow: discovery logging must never break a tool call
  }
}

export default {
  id: "polymorph-discovery",
  async setup(ctx) {
    const registrations = []

    // Register without ever letting a failure escape setup(): the call is made inside the
    // try, so a synchronous throw is caught as well as a rejected promise.
    const track = async (register) => {
      try {
        const registration = await register()
        if (registration) registrations.push(registration)
      } catch {}
    }

    await track(() =>
      ctx.tool.hook("execute.before", (event) => {
        pipeToPolymorph({
          hook_event_name: "execute.before",
          tool_name: event?.tool,
          session_id: event?.sessionID,
          agent: event?.agent,
          id: event?.id,
          messageID: event?.messageID,
          input: event?.input,
          // no native output arg exists on execute.before
        })
      }),
    )

    await track(() =>
      ctx.tool.hook("execute.after", (event) => {
        pipeToPolymorph({
          hook_event_name: "execute.after",
          tool_name: event?.tool,
          session_id: event?.sessionID,
          agent: event?.agent,
          id: event?.id,
          messageID: event?.messageID,
          input: event?.input,
          output: event?.result,
        })
      }),
    )

    // There is no session-start hook in this build; the closest native event
    // is the public "session.created" event, so that is logged as session
    // start. The stream is aborted when the plugin unloads.
    const controller = new AbortController()
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({
          types: ["session.created"],
          signal: controller.signal,
        })) {
          pipeToPolymorph({
            hook_event_name: "session.created",
            // Confirmed event envelope for this build:
            // { id, created, type: "session.created", data: { sessionID, info } }
            // The extra fallbacks are belt-and-braces in case a future build
            // flattens the payload.
            session_id:
              event?.data?.sessionID ??
              event?.data?.info?.id ??
              event?.sessionID ??
              event?.info?.id,
          })
        }
      } catch {
        // swallow: event-stream failures must never surface
      }
    })()

    return () => {
      try {
        controller.abort()
      } catch {}
      for (const registration of registrations) {
        try {
          registration.dispose()
        } catch {}
      }
    }
  },
}
