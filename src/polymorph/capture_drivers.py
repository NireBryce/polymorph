"""Harness drivers for `polymorph capture`.

A driver is code shipped with polymorph and selected by name only. Nothing a mapping, a
discovery log or a harness reply says ever picks, builds or extends a driver, a prompt or a
command. A driver has three parts: install the discovery logger into the scratch directory,
give the argv that runs one step headlessly, and remove the logger again.

Every driver logs to `<scratch>/.polymorph/discovery/<name>.jsonl` through
`<python> -m polymorph discover log`, with PYTHONPATH pointing at this polymorph so a source
checkout works without an install.
"""
from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path

from . import __version__, discover
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
        return scratch / discover.DISCOVERY_DIR / f"{self.name}.jsonl"

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
        # python is spawned directly; PYTHONPATH goes through the spawn `env` option, which is
        # the logger's whole environment
        args = ["-m", "polymorph", "discover", "log", *(["--full"] if full else []),
                "--out", str(self.log_path(scratch).resolve())]
        constants_block = (f"const POLYMORPH_CMD = {json.dumps(sys.executable)}\n"
                  f"const POLYMORPH_ARGS = {json.dumps(args)}\n"
                  f"const POLYMORPH_ENV = {json.dumps({'PYTHONPATH': PACKAGE_PARENT})}\n"
                  # provenance: the plugin sends it with every line (--plugin-version)
                  f"const PLUGIN_VERSION = {json.dumps(__version__)}")
        return template.replace(_CONSTANTS_MARK, constants_block)

    def install(self, scratch: Path, full: bool) -> None:
        plugin_dir = self._plugin_dir(scratch)
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "index.js").write_text(self.render_plugin(scratch, full), encoding="utf-8")

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
        config_file = ".claude/settings.json"
        for event in ("PreToolUse", "PostToolUse"):
            discover.init_config("claude", config_file, event, "*", base=scratch,
                                 log_dir=self.log_path(scratch).parent, redact=not full,
                                 pythonpath=PACKAGE_PARENT)

    def step_argv(self, prompt: str) -> list[str]:
        return [self.binary, "-p", prompt, "--permission-mode", "bypassPermissions",
                "--setting-sources", "project"]

    def remove(self, scratch: Path) -> None:
        shutil.rmtree(scratch / ".claude", ignore_errors=True)


def _zcode_cli_paths() -> tuple[Path, Path]:
    """(electron binary, zcode.cjs bundle) derived from the `zcode` wrapper on PATH.

    The wrapper is not the CLI: it boots the desktop app. The CLI is the app's own bundle
    run through Electron-as-node -- the same spawn the desktop uses for its agents
    (observed 2026-11-05). Layout as packaged for ZCode 3.17.2; anything else raises
    rather than guessing.
    """
    wrapper = shutil.which("zcode")
    if wrapper is None:
        raise RuntimeError("zcode is not on PATH")
    package_root = Path(wrapper).resolve().parent.parent   # <package>/bin/zcode -> <package>
    electron = package_root / "lib" / "ZCode" / "zcode"
    bundle = package_root / "lib" / "ZCode" / "resources" / "glm" / "zcode.cjs"
    if not (electron.is_file() and bundle.is_file()):
        raise RuntimeError(f"zcode package layout not recognized under {package_root}; expected "
                           "lib/ZCode/zcode and lib/ZCode/resources/glm/zcode.cjs")
    return electron, bundle


class ZCodeDriver(Driver):
    """ZCode headless CLI (`ELECTRON_RUN_AS_NODE=1 <package>/lib/ZCode/zcode
    <package>/lib/ZCode/resources/glm/zcode.cjs --prompt PROMPT --mode yolo`).

    The `zcode` on PATH is the desktop app's wrapper: `--version` and `--help` boot the
    GUI. The CLI is the app's bundle run through Electron-as-node, the spawn the desktop
    itself uses for its agents (headless probe 2026-11-05). The CLI's `version`
    subcommand prints 0.17.1 headless -- the agent runtime's version, a different number
    from the app's 3.17.2. `--mode` defaults to yolo for `--prompt` per the CLI's own
    help; it is passed explicitly but that default is not behaviorally observed. Hook
    trust works headless through the CLI's own `hooks trust grant` (docs/observed-zcode-session.md).

    BLOCKED on the last proof: every headless `--prompt` session in the probe died at
    model authentication before making a tool call -- the desktop injects the account
    provider over its protocol and the standalone CLI had none it could use -- so it is
    still not known that workspace hooks fire headlessly. Headless sessions also logged
    `config.project_hooks.pending_trust` for hooks every CLI trust surface calls trusted
    (an undetermined gate discrepancy), and capture's minimal env (PATH, HOME, XDG_*)
    drops the ZCODE_* provider overrides the desktop agent child carries -- whether the
    CLI finds its provider config under HOME alone is unconfirmed. A run could spend
    tokens and log nothing. The wiring freeze is satisfied by design: `install` runs
    before any step and each step is its own session, which reads the config at start.
    Hooks go in `<scratch>/.zcode/config.json` through the same logic as
    `polymorph discover init` in its claude-like shape (PreToolUse and PostToolUse).
    """

    name = "zcode"
    binary = "zcode"
    summary = ("ZCode: hooks in .zcode/config.json (claude-like), headless CLI via "
               "ELECTRON_RUN_AS_NODE; hooks firing headless unproven (BLOCKED)")
    untested = ("never run against a real zcode session; the argv is from the CLI's own help and "
                "the desktop's spawn code, read in the 2026-11-05 headless probe")
    blocked = ("no headless zcode session has been seen to make a tool call: the probe's sessions "
               "all died at model authentication, so whether workspace hooks fire headlessly is "
               "unconfirmed and a run could spend tokens and log nothing. Finishing the probe -- a "
               "working provider for the standalone CLI, one session that reaches a tool call -- is "
               "the unblock; use `polymorph capture --print-prompts` until then")

    def install(self, scratch: Path, full: bool) -> None:
        config_file = ".zcode/config.json"
        for event in ("PreToolUse", "PostToolUse"):
            discover.init_config("zcode", config_file, event, "*", base=scratch, shape="claude-like",
                                 log_dir=self.log_path(scratch).parent, redact=not full,
                                 pythonpath=PACKAGE_PARENT)

    def step_argv(self, prompt: str) -> list[str]:
        # `env` carries ELECTRON_RUN_AS_NODE because run_step's minimal env is built
        # before the driver is consulted; PATH is kept, so `env` itself resolves
        electron, bundle = _zcode_cli_paths()
        return ["env", "ELECTRON_RUN_AS_NODE=1", str(electron), str(bundle),
                "--prompt", prompt, "--mode", "yolo"]

    def version_argv(self) -> list[str]:
        electron, bundle = _zcode_cli_paths()
        return ["env", "ELECTRON_RUN_AS_NODE=1", str(electron), str(bundle), "version"]

    def remove(self, scratch: Path) -> None:
        shutil.rmtree(scratch / ".zcode", ignore_errors=True)


DRIVERS: dict[str, Driver] = {d.name: d for d in (OpenCodeDriver(), ClaudeDriver(), ZCodeDriver())}


def quoted(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)
