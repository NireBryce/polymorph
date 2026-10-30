"""Runtime adapter: native hook payload -> canonical (Claude) payload -> real hook -> native answer.

Faults in the adapter or its inputs never become a deny: they produce a visible
"NOT checked" message and a non-blocking exit.
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
        for ckey, npath in (mapping.get("payload_in") or {}).items():
            if ckey == "extractor":
                continue
            if not isinstance(npath, str):
                raise AdapterError(f"payload_in.{ckey} is not a path string")
            value = lookup(native, npath, None)
            if value is None:
                continue
            assign(canonical, CANON_PATHS.get(ckey, ckey), value)
        for key in PASSTHROUGH:
            if key not in canonical and key in native:
                canonical[key] = native[key]
        name = canonical.get("tool_name")
        if isinstance(name, str) and name not in CLAUDE_TOOLS:
            cls = _native_tool_class(mapping, name)
            if cls in CLASS_TOOL:
                canonical["tool_name"] = CLASS_TOOL[cls]
        ext = (mapping.get("payload_in") or {}).get("extractor")
        if ext is not None:
            for path, value in extractors.get(ext)(native).items():
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
    return {"stdout": "", "exit": 0, "stderr": text}


def not_checked(mapping: dict | None, why: str) -> dict:
    """Visible, non-blocking result for an adapter fault. Never a deny."""
    msg = f"polymorph adapter: this tool call was NOT checked by the guard hook ({why})."
    caps = (mapping or {}).get("capabilities") or {}
    stdout = json.dumps({"systemMessage": msg}) if caps.get("json_answers") else ""
    return {"stdout": stdout, "exit": 0 if stdout else 1, "stderr": msg}


def resolve_script(repo: Path, script: str) -> Path:
    hooks = (repo / ".agents" / "hooks").resolve()
    p = Path(script)
    real = (p if p.is_absolute() else repo / p).resolve()
    if not real.is_relative_to(hooks) or not real.is_file():
        raise AdapterError(f"hook script {script!r} is not a file under {hooks}; refused")
    return real


def find_repo(start: Path) -> Path:
    for d in [start, *start.parents]:
        if (d / ".agents").is_dir():
            return d
    raise AdapterError("no .agents directory found above the working directory")


_NAME = re.compile(r"[A-Za-z0-9_-]+")


def load_mapping(repo: Path, name: str, extra_dir: Path | None = None, trust: list[Path] = ()) -> dict:
    if not _NAME.fullmatch(name):
        raise AdapterError(f"bad harness name {name!r}")
    dirs = [repo / ".agents" / "polymorph" / "mappings", Path(__file__).resolve().parent.parent.parent / "mappings"]
    if extra_dir is not None:
        e = extra_dir.resolve()
        if e not in dirs and e not in [t.resolve() for t in trust]:
            raise AdapterError(f"--mappings {extra_dir} is outside trusted dirs; add --trust-dir")
        dirs.insert(0, e)
    for d in dirs:
        f = d / f"{name}.toml"
        if f.is_file():
            with open(f, "rb") as fh:
                return tomllib.load(fh)
    raise AdapterError(f"no mapping {name}.toml found")


def run_hook(script: Path, canonical: dict, env_extra: dict, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_extra}
    return subprocess.run(
        [str(script)], input=json.dumps(canonical), capture_output=True, text=True,
        env=env, cwd=cwd, timeout=HOOK_TIMEOUT,
    )


def adapt(mapping: dict, repo: Path, script: str, stdin_text: str) -> dict:
    real = resolve_script(repo, script)
    try:
        native = json.loads(stdin_text)
    except json.JSONDecodeError as e:
        raise AdapterError(f"native payload is not valid JSON: {e}") from e
    canonical = translate_in(mapping, native)
    try:
        cp = run_hook(real, canonical, capability_env(mapping), repo)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AdapterError(f"hook could not run: {e}") from e
    if cp.returncode == 2:
        answer = {"hookSpecificOutput": {"permissionDecision": "deny",
                                         "permissionDecisionReason": cp.stderr.strip()}}
        return translate_out(mapping, answer)
    if cp.returncode != 0:
        raise AdapterError(f"hook exited {cp.returncode}: {cp.stderr.strip()[:300]}")
    out = cp.stdout.strip()
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
    ap = argparse.ArgumentParser(prog="polymorph adapter")
    ap.add_argument("--harness", required=True)
    ap.add_argument("--mappings", type=Path)
    ap.add_argument("--trust-dir", type=Path, action="append", default=[])
    ap.add_argument("--repo", type=Path)
    ap.add_argument("script", help="hook script (after --)")
    args = ap.parse_args(argv)
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    mapping = None
    try:
        repo = (args.repo or find_repo(Path.cwd())).resolve()
        mapping = load_mapping(repo, args.harness, args.mappings, args.trust_dir)
        result = adapt(mapping, repo, args.script, stdin.read())
    except AdapterError as e:
        result = not_checked(mapping, str(e))
    except Exception as e:  # any fault: still visible, still never a deny
        result = not_checked(mapping, f"internal error: {type(e).__name__}: {e}")
    if result["stdout"]:
        stdout.write(result["stdout"] + ("" if result["stdout"].endswith("\n") else "\n"))
    if result["stderr"]:
        stderr.write(result["stderr"] + "\n")
    return result["exit"]


if __name__ == "__main__":
    sys.exit(run_adapter())
