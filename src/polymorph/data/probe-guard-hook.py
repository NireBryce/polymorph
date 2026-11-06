"""Guard hook for `polymorph discover probe`; its constants are written in at render time.

The probe wires this script where the harness's decision hooks run, beside the discovery
logger. It acts only on a `tool_input.command` that carries this run's token: any other
command exits 0 before anything else is read, so the probe can never block a real call,
and any failure of its own exits 0 too (a catch-all hook that fails closed would block
every call in the session). A dispatched case writes one decode line to DECODE_PATH --
case, event, the answer this branch chose, the call id -- the join key between marker and
payload, per docs/discovery.md, "Capability probes and a decode file". The side file
carries markers, ids and the probe's own chosen answers, never payload values.
"""
#@@CONSTANTS@@#
import json
import sys

CASE_MARKS = {case: "PPROBE-" + case.upper() + "-" + TOKEN for case in CASES}


def _call_id(payload):
    for key in ("tool_use_id", "toolCallId", "id", "call_id"):
        value = payload.get(key)
        if isinstance(value, str) and 0 < len(value) <= 128:
            return value
    return "no-id"


def _event(payload):
    for key in ("hook_event_name", "hookEventName", "event"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return "unknown-event"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return 0
    case = next((c for c in CASES if CASE_MARKS[c] in command), None)
    if case is None:
        return 0   # not a probe command: pass it, whatever it is
    try:
        with open(DECODE_PATH, "a", encoding="utf-8") as f:
            f.write(f"{case} {_event(payload)} {ANSWERS[case]} {_call_id(payload)}\n")
    except OSError:
        pass
    if case == "deny":
        print("polymorph probe: this probe command is denied by the probe guard", file=sys.stderr)
        return 2
    answer = {"json": JSON_DENY, "ask": JSON_ASK, "rewrite": JSON_REWRITE}.get(case)
    if answer is not None:
        print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
