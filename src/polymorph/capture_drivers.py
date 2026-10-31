"""Harness drivers for `polymorph capture`.

A driver is code shipped with polymorph and selected by name only. Nothing a mapping, a
discovery log or a harness reply says ever picks, builds or extends a driver, a prompt or a
command. A driver has three parts: install the discovery logger into the scratch directory,
give the argv that runs one step headlessly, and remove the logger again.

Every driver logs to `<scratch>/.polymorph-discovery/<name>.jsonl` through
`<python> -m polymorph discover log`, with PYTHONPATH pointing at this polymorph so a source
checkout works without an install.
"""
from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path

from . import discover
from .resources import DATA_DIR

PACKAGE_PARENT = str(Path(__file__).resolve().parent.parent)   # the directory holding polymorph/
_CONSTANTS_MARK = "/*@@CONSTANTS@@*/"


class Driver:
    name = ""
    binary = ""
    summary = ""
    untested = ""   # non-empty: why this driver has never run against the real harness
    blocked = ""    # non-empty: `capture` refuses to run this driver, and says why

    def log_path(self, scratch: Path) -> Path:
        return scratch / ".polymorph-discovery" / f"{self.name}.jsonl"

    def install(self, scratch: Path, full: bool) -> None:
        raise NotImplementedError

    def step_argv(self, prompt: str) -> list[str]:
        raise NotImplementedError

    def version_argv(self) -> list[str]:
        return [self.binary, "--version"]

    def remove(self, scratch: Path) -> None:
        raise NotImplementedError


class OpenCodeDriver(Driver):
    """OpenCode v2 (`opencode run --standalone --auto PROMPT`).

    `--standalone` gives the run a private server, so the plugin sees only this run's tool
    calls (a shared server logs every session on it). The plugin is the one in
    docs/examples/opencode-discovery-plugin.js with absolute paths written in.
    Flags as listed by `opencode run --help` for v2.0.32; the plugin load path is from
    docs/observed-opencode.md. The tool has not been run against OpenCode by the tests.
    """

    name = "opencode"
    binary = "opencode"
    summary = "OpenCode v2: logging plugin in .opencode/plugins/, `opencode run --standalone --auto`"

    def _plugin_dir(self, scratch: Path) -> Path:
        return scratch / ".opencode" / "plugins" / "polymorph-discovery"

    def render_plugin(self, scratch: Path, full: bool) -> str:
        template = (DATA_DIR / "opencode-discovery-plugin.js").read_text(encoding="utf-8")
        if template.count(_CONSTANTS_MARK) != 1:
            raise RuntimeError("opencode plugin template is missing its constants marker")
        # `env` sets PYTHONPATH without changing the plugin code under the constants
        args = ["PYTHONPATH=" + PACKAGE_PARENT, sys.executable, "-m", "polymorph", "discover", "log",
                *(["--full"] if full else []), "--out", str(self.log_path(scratch).resolve())]
        consts = (f"const POLYMORPH_CMD = {json.dumps('/usr/bin/env')}\n"
                  f"const POLYMORPH_ARGS = {json.dumps(args)}")
        return template.replace(_CONSTANTS_MARK, consts)

    def install(self, scratch: Path, full: bool) -> None:
        d = self._plugin_dir(scratch)
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.js").write_text(self.render_plugin(scratch, full), encoding="utf-8")

    def step_argv(self, prompt: str) -> list[str]:
        return [self.binary, "run", "--standalone", "--auto", prompt]

    def remove(self, scratch: Path) -> None:
        shutil.rmtree(self._plugin_dir(scratch), ignore_errors=True)


class ClaudeDriver(Driver):
    """Claude Code (`claude -p PROMPT --permission-mode bypassPermissions --setting-sources project`).

    UNTESTED. This driver has never been run against a real `claude`. Its flags were read from
    `claude --help` (2.1.314 at the time): `-p/--print`, `--permission-mode` with the choice
    `bypassPermissions`, `--setting-sources`, `--version`. Not confirmed by a run: that hooks fire
    in `-p` mode under `bypassPermissions`, and that `--setting-sources project` still finds the
    login. Hooks go in `<scratch>/.claude/settings.json` through the same logic as
    `polymorph discover init` (PreToolUse and PostToolUse).
    """

    name = "claude"
    binary = "claude"
    summary = "Claude Code: hooks in .claude/settings.json, `claude -p` (UNTESTED)"
    untested = "never run against a real claude; flags taken from `claude --help` only"
    blocked = ("it has never run against a real claude, it needs --permission-mode bypassPermissions, "
               "and it is not known that hooks fire under `claude -p`, so a run could spend tokens and "
               "log nothing. Until it is confirmed live, use `polymorph capture --print-prompts`")

    def install(self, scratch: Path, full: bool) -> None:
        cfg = ".claude/settings.json"
        for event in ("PreToolUse", "PostToolUse"):
            discover.init_config("claude", cfg, event, "*", base=scratch,
                                 log_dir=self.log_path(scratch).parent, redact=not full,
                                 pythonpath=PACKAGE_PARENT)

    def step_argv(self, prompt: str) -> list[str]:
        return [self.binary, "-p", prompt, "--permission-mode", "bypassPermissions",
                "--setting-sources", "project"]

    def remove(self, scratch: Path) -> None:
        shutil.rmtree(scratch / ".claude", ignore_errors=True)


DRIVERS: dict[str, Driver] = {d.name: d for d in (OpenCodeDriver(), ClaudeDriver())}


def quoted(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)
