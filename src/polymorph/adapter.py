"""Runtime adapter: native hook payload -> canonical (Claude) payload -> real hook -> native answer.

A fault in the adapter or its inputs (bad payload, crashing hook, timeout, unreadable
mapping) fails closed by default: the call is blocked with a visible reason, rendered
through the mapping's own deny route. `--fail-open` is the opt-in other way: the call
proceeds with a visible "NOT checked" message. Either way the fault is never silent.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from . import extractors
from .canonical import CANON_PATHS
from .exprs import ExprError, assign, lookup, render
from .resources import MAPPINGS_DIR

CAPS = {
    "ask": "GUARD_ASK_ENFORCED",
    "post_event": "GUARD_POST_EVENT",
    "exit_2_blocks": "GUARD_EXIT2_BLOCKS",
    "rewrites_input": "GUARD_REWRITES_INPUT",
    "json_answers": "GUARD_JSON_ANSWERS",
}

# Canonical Claude tool name per class, used when the native name is not a Claude one.
CLASS_TOOL = {"shell": "Bash", "edit": "Edit", "read": "Read"}
CLAUDE_TOOLS = {"Bash", "Edit", "Write", "MultiEdit", "Read", "Grep", "Glob"}
PASSTHROUGH = ("tool_response", "session_id", "cwd")
HOOK_TIMEOUT = 60


class AdapterError(Exception):
    pass


def capability_env(mapping: dict) -> dict[str, str]:
    caps = mapping.get("capabilities", {}) or {}
    return {var: "1" if caps.get(key) else "0" for key, var in CAPS.items()}


def _native_tool_class(mapping: dict, name: str) -> str | None:
    for cls, names in (mapping.get("tools") or {}).items():
        if name == names or (isinstance(names, list) and name in names):
            return cls
    return None


def translate_in(mapping: dict, native: Any) -> dict:
    if not isinstance(native, dict):
        raise AdapterError("native payload is not a JSON object")
    canonical: dict[str, Any] = {}
    try:
        for canon_key, native_path in (mapping.get("payload_in") or {}).items():
            if canon_key == "extractor":
                continue
            if not isinstance(native_path, str):
                raise AdapterError(f"payload_in.{canon_key} is not a path string")
            value = lookup(native, native_path, None)
            if value is None:
                continue
            assign(canonical, CANON_PATHS.get(canon_key, canon_key), value)
        for key in PASSTHROUGH:
            if key not in canonical and key in native:
                canonical[key] = native[key]
        name = canonical.get("tool_name")
        if isinstance(name, str) and name not in CLAUDE_TOOLS:
            cls = _native_tool_class(mapping, name)
            if cls in CLASS_TOOL:
                canonical["tool_name"] = CLASS_TOOL[cls]
        extractor_name = (mapping.get("payload_in") or {}).get("extractor")
        if extractor_name is not None:
            for path, value in extractors.get(extractor_name)(native).items():
                assign(canonical, path, value)
    except (ExprError, extractors.ExtractorError) as e:
        raise AdapterError(str(e)) from e
    return canonical


def _classify(answer: dict | None) -> tuple[str, dict[str, Any]]:
    """Return (kind, placeholder values). kind: allow|ask|deny|warn|remind|none."""
    if not answer:
        return "none", {}
    hso = answer.get("hookSpecificOutput") or {}
    vals = {
        "reason": hso.get("permissionDecisionReason") or answer.get("reason") or "",
        "context": hso.get("additionalContext") or answer.get("additionalContext") or "",
        "message": answer.get("systemMessage") or "",
    }
    decision = hso.get("permissionDecision")
    if decision in ("allow", "ask", "deny"):
        return decision, vals
    if answer.get("decision") == "block":
        return "deny", vals
    if vals["context"]:
        return "warn", vals
    if vals["message"]:
        return "remind", vals
    return "none", vals


def translate_out(mapping: dict, answer: dict | None) -> dict:
    kind, vals = _classify(answer)
    if kind == "none":
        if answer is not None:
            # the hook spoke, and nothing in its answer maps to a native form:
            # a silent allow here would discard the guard's verdict
            raise AdapterError(
                f"hook answer has no decision polymorph can translate: {json.dumps(answer)[:200]}")
        return {"stdout": "", "exit": 0, "stderr": ""}
    spec = (mapping.get("payload_out") or {}).get(kind)
    caps = mapping.get("capabilities") or {}
    if isinstance(spec, dict):
        try:
            stderr = render(spec.get("stderr", ""), vals)
            stdout = render(spec["stdout"], vals) if "stdout" in spec else None
        except ExprError as e:
            raise AdapterError(str(e)) from e
        out = "" if stdout is None else (stdout if isinstance(stdout, str) else json.dumps(stdout))
        return {"stdout": out, "exit": int(spec.get("exit", 0)), "stderr": str(stderr)}
    # No mapping entry: pass the canonical answer through when the harness speaks it.
    text = vals["reason"] or vals["context"] or vals["message"]
    if caps.get("json_answers"):
        return {"stdout": json.dumps(answer), "exit": 0, "stderr": ""}
    if kind == "deny" and caps.get("exit_2_blocks"):
        return {"stdout": "", "exit": 2, "stderr": text}
    if kind == "deny":
        # no payload_out.deny and no way to block: exit 0 here would turn the guard's deny into an allow
        raise AdapterError(f"the mapping has no way to express a deny (no payload_out.deny, "
                           f"exit_2_blocks and json_answers false): {text or 'no reason given'}")
    return {"stdout": "", "exit": 0, "stderr": text}


def blocked_on_fault(mapping: dict | None, why: str) -> dict:
    """The default result for an adapter fault: block the call, with the reason visible.

    The block is the mapping's own deny route, so it reaches the harness in the form that
    harness honours (zcode: exit 2 and stderr, since it ignores JSON). Without a usable
    mapping, or one that cannot express a deny, the canonical block is exit 2 and stderr.
    """
    msg = (f"polymorph adapter: this tool call was BLOCKED because the guard hook could not run ({why}). "
           "Fix the guard, or set fail_open for this harness in .polymorph/basecamp/config.toml.")
    if mapping:
        try:
            return translate_out(mapping, {"hookSpecificOutput": {"permissionDecision": "deny",
                                                                  "permissionDecisionReason": msg}})
        except AdapterError:
            pass
    return {"stdout": "", "exit": 2, "stderr": msg}


def not_checked(mapping: dict | None, why: str) -> dict:
    """The --fail-open result for an adapter fault: visible, non-blocking. Never a deny."""
    msg = f"polymorph adapter: this tool call was NOT checked by the guard hook ({why})."
    caps = (mapping or {}).get("capabilities") or {}
    stdout = json.dumps({"systemMessage": msg}) if caps.get("json_answers") else ""
    return {"stdout": stdout, "exit": 0 if stdout else 1, "stderr": msg}


def resolve_script(repo: Path, script: str) -> Path:
    hooks_dir = (repo / ".agents" / "hooks").resolve()
    script_path = Path(script)
    real = (script_path if script_path.is_absolute() else repo / script_path).resolve()
    if not real.is_relative_to(hooks_dir) or not real.is_file():
        raise AdapterError(f"hook script {script!r} is not a file under {hooks_dir}; refused")
    return real


def find_repo(start: Path) -> Path:
    for candidate_dir in [start, *start.parents]:
        if (candidate_dir / ".agents").is_dir():
            return candidate_dir
    raise AdapterError("no .agents directory found above the working directory")


_NAME = re.compile(r"[A-Za-z0-9_-]+")


def load_mapping(repo: Path, name: str, extra_dir: Path | None = None, trust: list[Path] = ()) -> dict:
    from .validate import validate_mapping

    if not _NAME.fullmatch(name):
        raise AdapterError(f"bad harness name {name!r}")
    # every compared root is resolved: a resolved --mappings against unresolved
    # base dirs refused the repo's own mappings dir reached through a symlink
    dirs = [(repo / ".polymorph" / "basecamp" / "mappings").resolve(), MAPPINGS_DIR.resolve()]
    if extra_dir is not None:
        extra_resolved = extra_dir.resolve()
        if extra_resolved not in dirs and extra_resolved not in [trust_dir.resolve() for trust_dir in trust]:
            raise AdapterError(f"--mappings {extra_dir} is outside trusted dirs; add --trust-dir")
        dirs.insert(0, extra_resolved)
    for candidate_dir in dirs:
        candidate_path = candidate_dir / f"{name}.toml"
        if candidate_path.is_file():
            with open(candidate_path, "rb") as handle:
                raw = tomllib.load(handle)
            validation_errors = validate_mapping(raw)
            if validation_errors:
                # an invalid mapping must be a visible NOT-checked, never a silently
                # mis-wired guard: validation here is the runtime trust boundary
                raise AdapterError(f"mapping {name} is invalid: {'; '.join(validation_errors)}")
            return raw
    raise AdapterError(f"no mapping {name}.toml found")


def run_hook(script: Path, canonical: dict, env_extra: dict, cwd: Path,
             timeout: float = HOOK_TIMEOUT) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [str(script)], input=json.dumps(canonical), capture_output=True, text=True,
        env=env, cwd=cwd, timeout=timeout,
    )


def adapt(mapping: dict, repo: Path, script: str, stdin_text: str,
          timeout: float = HOOK_TIMEOUT) -> dict:
    real = resolve_script(repo, script)
    try:
        native = json.loads(stdin_text)
    except json.JSONDecodeError as e:
        raise AdapterError(f"native payload is not valid JSON: {e}") from e
    canonical = translate_in(mapping, native)
    try:
        proc = run_hook(real, canonical, capability_env(mapping), repo, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AdapterError(f"hook could not run: {e}") from e
    if proc.returncode == 2:
        answer = {"hookSpecificOutput": {"permissionDecision": "deny",
                                         "permissionDecisionReason": proc.stderr.strip()}}
        return translate_out(mapping, answer)
    if proc.returncode != 0:
        raise AdapterError(f"hook exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    out = proc.stdout.strip()
    answer = None
    if out:
        try:
            answer = json.loads(out)
        except json.JSONDecodeError as e:
            raise AdapterError(f"hook stdout is not JSON: {e}") from e
        if not isinstance(answer, dict):
            raise AdapterError("hook stdout is not a JSON object")
    return translate_out(mapping, answer)


def run_adapter(argv: list[str] | None = None, stdin=None, stdout=None, stderr=None) -> int:
    parser = argparse.ArgumentParser(prog="polymorph adapter")
    parser.add_argument("--harness", required=True)
    parser.add_argument("--mappings", type=Path)
    parser.add_argument("--trust-dir", type=Path, action="append", default=[])
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--timeout", type=float, default=HOOK_TIMEOUT,
                        help="seconds before the hook is killed (a killed hook is NOT checked, never a deny)")
    parser.add_argument("--fail-open", action="store_true",
                        help="on an adapter or hook fault, let the call run with a NOT-checked message "
                             "(default: block it)")
    parser.add_argument("script", help="hook script (after --)")
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    mapping = None
    # read before parsing: a usage error must still honour the flag the command line carried
    fail_open = "--fail-open" in (argv or [])[:(argv or []).index("--") if "--" in (argv or []) else None]
    on_fault = not_checked if fail_open else blocked_on_fault
    try:
        try:
            args = parser.parse_args(argv)
        except SystemExit as e:
            # a usage error must land in the same fault path as every other fault, not in
            # argparse's own exit 2
            raise AdapterError(f"usage error ({e.code})") from None
        if not (args.timeout > 0) or args.timeout in (float("inf"), float("nan")):
            raise AdapterError("--timeout must be finite and above 0")
        repo = (args.repo or find_repo(Path.cwd())).resolve()
        mapping = load_mapping(repo, args.harness, args.mappings, args.trust_dir)
        result = adapt(mapping, repo, args.script, stdin.read(), timeout=args.timeout)
    except AdapterError as e:
        result = on_fault(mapping, str(e))
    except Exception as e:  # any fault: still visible
        result = on_fault(mapping, f"internal error: {type(e).__name__}: {e}")
    if result["stdout"]:
        stdout.write(result["stdout"] + ("" if result["stdout"].endswith("\n") else "\n"))
    if result["stderr"]:
        stderr.write(result["stderr"] + "\n")
    return result["exit"]


if __name__ == "__main__":
    sys.exit(run_adapter())
