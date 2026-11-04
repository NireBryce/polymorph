// polymorph capture: the OpenCode discovery plugin, installed into a scratch directory
// for one capture and removed afterwards. Derived from
// docs/examples/opencode-discovery-plugin.js (tests/test_capture.py checks the code below
// the constants is identical); only the constants differ: the absolute command, arguments
// and logger environment are written by `polymorph capture`, never read from anywhere else.
// It never throws, never blocks or alters a tool call, never prints, and pipes each
// payload only to the `polymorph discover log` command.
//
import { spawn } from "node:child_process"

/*@@CONSTANTS@@*/

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
    const child = spawn(POLYMORPH_CMD, [...POLYMORPH_ARGS, ...PROVENANCE_ARGS], {
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

// Provenance for the log: this plugin generation's version and where the session runs
// (OpenCode's event bus is server-global, so one log can hold lines from several working
// directories). Best-effort: ctx.location is read defensively, a value the logger does not
// accept becomes <invalid> on the line, and a missing one reads as unmarked in the summary.
let PROVENANCE_ARGS = []

function setProvenance(ctx) {
  try {
    const args = []
    if (typeof PLUGIN_VERSION === "string" && PLUGIN_VERSION) {
      args.push("--plugin-version=" + PLUGIN_VERSION)
    }
    if (typeof ctx?.location === "string" && ctx.location) {
      args.push("--location=" + ctx.location)
    }
    PROVENANCE_ARGS = args
  } catch {
    PROVENANCE_ARGS = []
  }
}

export default {
  id: "polymorph-discovery",
  async setup(ctx) {
    setProvenance(ctx)
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
