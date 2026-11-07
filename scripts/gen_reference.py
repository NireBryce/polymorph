#!/usr/bin/env python3
"""Write the command reference from the real argparse parser, so it cannot drift from the program.

    python3 scripts/gen_reference.py           # write docs/usage.md and man/polymorph.1
    python3 scripts/gen_reference.py --check   # exit 1 if either file differs from what the parser says
    just reference | just reference-check

Everything about commands and flags (names, metavars, help text, which flag takes a value)
comes from `polymorph.cli._parser()`. The few sentences that are not about a flag (what the tool
is, the files it reads, the exit statuses) are the constants below; `docs/cli.md` is the
narrative reference and these files are the mechanical one.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from polymorph import __version__, cli  # noqa: E402

USAGE_MD = "docs/usage.md"
MAN = "man/polymorph.1"

NOTICE = ("polymorph is experimental and unvetted. It sits on a safety path (guard hooks), where a "
          "wrong mapping can fail open. Nothing here promises compatibility.")
DESCRIPTION = ("polymorph reads a repository's Claude Code agent config (.agents/settings.json and "
               ".agents/hooks/) and writes hook configs for other coding harnesses from declarative "
               "per-harness TOML mappings. It also reports what else the repository provides (skills, "
               "rules, memory files) that a harness will not read, and can run a hook script behind a "
               "mapping at hook time.")
FILES = (
    (".agents/settings.json", "The Claude Code settings read by manage generate and manage check."),
    (".agents/hooks/", "Hook scripts. A hook must resolve under this directory."),
    (".polymorph/basecamp/config.toml", "The harnesses manage generate writes for."),
    (".polymorph/basecamp/mappings/", "Repo-local mappings; they override the built-in ones with a warning."),
    (".polymorph/basecamp/decisions/NAME.toml", "Recorded answers to generation-time mismatches."),
    (".polymorph/basecamp/coverage/NAME.toml", "What a harness reads of skills, rules and memory files."),
    (".polymorph/basecamp/env.toml", "Host facts for the env contract."),
    (".polymorph/basecamp/loadout.toml", "Provenance of what equip issued."),
    (".polymorph/discovery/", "Default directory for discover logs."),
)
EXIT_STATUS = (
    ("0", "Success."),
    ("1", "Findings or drift (manage check, manage generate --check), a refusal (manage adopt), "
          "a failed manage conformance run."),
    ("2", "A usage error or an error in the input. The adapter never exits 2: its faults are "
          "reported as NOT checked."),
    ("3", "manage generate has undecided items and wrote nothing for those targets."),
)


# ---- reading the parser -----------------------------------------------------

def _sub_action(parser: argparse.ArgumentParser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a
    return None


def commands(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()):
    """(words, parser, help) for every subcommand, parents before children."""
    sub = _sub_action(parser)
    if sub is None:
        return
    help_by_dest = {action.dest: action.help for action in sub._choices_actions}
    for name, child in sub.choices.items():
        words = (*prefix, name)
        yield words, child, help_by_dest.get(name) or ""
        yield from commands(child, words)


def _takes_value(action: argparse.Action) -> bool:
    return action.nargs != 0


def _metavar(action: argparse.Action) -> str:
    if action.metavar:
        return action.metavar if isinstance(action.metavar, str) else " ".join(action.metavar)
    if action.choices:
        return "{" + ",".join(map(str, action.choices)) + "}"
    return action.dest.upper().replace("-", "_")


def options(parser: argparse.ArgumentParser):
    """Option actions (not -h, not the subcommand chooser)."""
    return [action for action in parser._actions
            if action.option_strings and not isinstance(action, argparse._HelpAction)]


def positionals(parser: argparse.ArgumentParser):
    return [action for action in parser._actions
            if not action.option_strings and not isinstance(action, argparse._SubParsersAction)]


def _flag(action: argparse.Action) -> str:
    name = max(action.option_strings, key=len)
    return f"{name} {_metavar(action)}" if _takes_value(action) else name


def usage_line(words: tuple[str, ...], parser: argparse.ArgumentParser) -> str:
    parts = ["polymorph", *words]
    for action in options(parser):
        text = _flag(action) + (" ..." if isinstance(action, argparse._AppendAction) else "")
        parts.append(text if action.required else f"[{text}]")
    if _sub_action(parser) is not None:
        parts.append("COMMAND")
    for action in positionals(parser):
        name = action.metavar or action.dest
        parts.append(f"{name}..." if action.nargs in ("+", "*") else name)
    return " ".join(parts)


def _help(action: argparse.Action) -> str:
    return (action.help or "").replace("%(default)s", str(action.default))


# ---- markdown ---------------------------------------------------------------

def usage_md() -> str:
    parser = cli._parser()
    out = [
        "# Usage reference",
        "",
        "<!-- Generated by scripts/gen_reference.py from the argparse parser. Do not edit; run `just reference`. -->",
        "",
        f"> {NOTICE}",
        "",
        "This page lists every command and flag exactly as the program defines them. "
        "[The CLI reference](cli.md) explains what they do.",
        "",
    ]
    for words, subparser, helptext in commands(parser):
        out += [f"## polymorph {' '.join(words)}", ""]
        if helptext:
            out += [helptext[0].upper() + helptext[1:] + ("" if helptext.endswith(".") else "."), ""]
        out += ["```", usage_line(words, subparser), "```", ""]
        positional_actions, option_actions = positionals(subparser), options(subparser)
        if positional_actions or option_actions:
            out += ["| Argument | Meaning |", "|---|---|"]
            for action in positional_actions:
                out.append(f"| `{action.metavar or action.dest}` | {_help(action).replace('|', '/')} |")
            for action in option_actions:
                out.append(f"| `{_flag(action)}` | {_help(action).replace('|', '/')} |")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---- man page ---------------------------------------------------------------

def esc(text: str) -> str:
    text = text.replace("\\", "\\e").replace("-", "\\-")
    return "\n".join("\\&" + ln if ln[:1] in (".", "'") else ln for ln in text.split("\n"))


def man_page() -> str:
    parser = cli._parser()
    cmds = list(commands(parser))
    out = [
        '.\\" Generated by scripts/gen_reference.py from the argparse parser. Do not edit; run `just reference`.',
        f'.TH POLYMORPH 1 "" "polymorph {esc(__version__)}" "User Commands"',
        ".SH NAME",
        "polymorph \\- write hook configs for other coding harnesses from a repository's Claude Code config",
        ".SH SYNOPSIS",
    ]
    for words, subparser, _ in cmds:
        if _sub_action(subparser) is None:
            out += [".br", esc(usage_line(words, subparser))]
    out += [".SH DESCRIPTION", esc(DESCRIPTION), ".PP", ".B " + esc(NOTICE), ".SH COMMANDS"]
    for words, subparser, helptext in cmds:
        out += [f".SS polymorph {esc(' '.join(words))}"]
        if helptext:
            out.append(esc(helptext[0].upper() + helptext[1:] + ("" if helptext.endswith(".") else ".")))
            out.append(".PP")
        out += [".nf", esc(usage_line(words, subparser)), ".fi"]
        for action in [*positionals(subparser), *options(subparser)]:
            label = action.metavar or action.dest if not action.option_strings else _flag(action)
            out += [".TP", ".B " + esc(label), esc(_help(action))]
    out += [".SH FILES"]
    for path, what in FILES:
        out += [".TP", ".I " + esc(path), esc(what)]
    out += [".SH EXIT STATUS"]
    for code, what in EXIT_STATUS:
        out += [".TP", ".B " + code, esc(what)]
    out += [".SH SEE ALSO",
            "docs/cli.md (what each command does), docs/usage.md (this page as Markdown), docs/SPEC.md (the contract).",
            ""]
    return "\n".join(out)


# ---- driver -----------------------------------------------------------------

def build() -> dict[str, str]:
    return {USAGE_MD: usage_md(), MAN: man_page()}


def stale(root: Path = ROOT) -> list[str]:
    """Repo-relative paths whose content differs from what the parser produces."""
    bad = []
    for rel, text in build().items():
        path = root / rel
        if not path.is_file() or path.read_text() != text:
            bad.append(rel)
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 if a file is out of date")
    a = ap.parse_args(argv)
    if a.check:
        bad = stale()
        for rel in bad:
            print(f"{rel}: out of date; run `just reference`", file=sys.stderr)
        return 1 if bad else 0
    for rel, text in build().items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
