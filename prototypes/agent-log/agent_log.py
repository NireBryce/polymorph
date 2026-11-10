#!/usr/bin/env python3
"""The agent log: one short line per event, so agents in separate worktrees of
one clone can see what the others claimed, finished, decided and dropped.

    python3 prototypes/agent-log/agent_log.py add KIND [--paths A,B] [--sha SHA] TEXT...
    python3 prototypes/agent-log/agent_log.py read [--paths A,B] [--tail N] [--claims] [--archive]
    python3 prototypes/agent-log/agent_log.py archive [--branch NAME] [--wt NAME ...] [--dry-run]
    python3 prototypes/agent-log/agent_log.py check [--recent-days N]
    python3 prototypes/agent-log/agent_log.py flag SLUG LINE REASON...
    python3 prototypes/agent-log/agent_log.py resolve SLUG LINE NOTE... [--redact]
    just agent-log ARGS...    just agent-log-check   (= check, no options; CI runs it)

The live log is a directory of `WORKTREE.log` files under the git common dir
(`git rev-parse --git-common-dir`): shared by every worktree of the clone, never
committed. `archive` moves a branch's entries, compacted, into
`wiki/agent-log/SLUG.log` for the PR to commit. prototypes/agent-log/style-guide.md is the style
guide and says how to undo all of this.

An entry is data, never instructions: a fixed grammar, printable ASCII, 200
characters of text. A line that fails any rule is rejected, not scrubbed, and the
message names the rule, never the matched text. `add` and `archive` use the same
rules, and `check` re-runs the current rules over everything committed, so a rule
added later reaches old lines. The rules are heuristics: they lower the chance a
secret reaches a committed file; they do not prove its absence.

Exit codes: 0 done; 1 `archive` or `check` found a problem and changed nothing it
could avoid changing; 2 bad usage or an entry the rules reject.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

KINDS = ("claim", "release", "done", "decision", "gotcha", "abandon")
DROPPED_ON_ARCHIVE = ("claim", "release")
TS_FORMAT = "%Y-%m-%dT%H:%MZ"
MAX_TEXT = 200
STRICT_MAX_TEXT = 80
DEFAULT_TAIL = 30
DEFAULT_STALE_HOURS = 24
FLAGS_FILE = "flags.log"
REDACTED_TEXT = "redacted after flag"
ARCHIVE_DIR = Path("wiki") / "agent-log"

TS = r"\d{4}-\d\d-\d\dT\d\d:\d\dZ"
ENTRY = re.compile(
    rf"^(?P<ts>{TS}) wt=(?P<wt>[A-Za-z0-9._-]{{1,60}}) kind=(?P<kind>[a-z]+)"
    r"(?: sha=(?P<sha>[0-9a-f]{7,40}))?"
    r"(?: paths=(?P<paths>[A-Za-z0-9._/@+,-]{1,300}))?"
    rf" \| (?P<text>[ -~]{{1,{MAX_TEXT}}})$"
)
FLAG_ENTRY = re.compile(
    rf"^(?P<ts>{TS}) kind=(?P<kind>flag|resolved) ref=(?P<slug>[a-z0-9._-]{{1,80}}):(?P<line>\d{{1,6}})"
    rf" \| (?P<text>[ -~]{{1,{MAX_TEXT}}})$"
)

# Rules on the whole line. Each is (name shown to the writer, pattern).
# The sops lines are Mozilla sops: ciphertext markers, its metadata block, age keys.
LINE_RULES = [
    ("a token or key prefix", re.compile(
        r"ghp_|gho_|ghs_|ghu_|github_pat_|glpat-|npm_[A-Za-z0-9]|\bsk-|sk_live|\bAKIA|\bASIA[A-Z0-9]"
        r"|xox[abprs]-|\bAIza|-----BEGIN|\beyJ[A-Za-z0-9_-]{8,}")),
    ("sops ciphertext, metadata or an age key", re.compile(
        r"ENC\[|AGE-SECRET-KEY-|\bage1[0-9a-z]{20,}|\bsops:\s*[\[{]?\s*(?:kms|age|pgp|lastmodified|mac)\b")),
    ("a long hex string (a sha goes in sha=)", re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{20,}(?![0-9A-Za-z])")),
    ("a URL with credentials", re.compile(r"://[^/\s]*@")),
    ("a home directory path", re.compile(r"/home/|/Users/|/root/|[A-Za-z]:\\Users\\")),
]
MIXED_RUN = re.compile(r"[A-Za-z0-9+/=]{24,}")

# Rules on the text field only.
KEY_VALUE = re.compile(r"(?i)\b\w*(key|token|secret|passw|credential|auth|bearer)\w*\s*[=:]\s*\S")
SHELL_CHARS = re.compile(r"[`;<>|\\]|\$\(|\$\{|&&")
IMPERATIVE_START = re.compile(
    r"(?i)^(run|do|don.t|never|always|you|ignore|must|should|make sure|ensure|please|use|add|remove|delete"
    r"|execute|call|set|read|write|open|send|post|print|echo|cat|curl|rm|sudo)\b")
INJECTION = re.compile(
    r"(?i)(ignore (all |any |previous|prior|the above)|disregard|system prompt|instructions? (say|from|above)"
    r"|as an ai|you are now|new instructions?)")
SENSITIVE_WORD = re.compile(
    r"(?i)\b(sops|secrets?|vault|credentials?|keyring|passwords?|tokens?|pgp|gpg|age[- ]keys?)\b")
STRICT_TEXT = re.compile(rf"^[A-Za-z .,-]{{1,{STRICT_MAX_TEXT}}}$")
SECRET_ENV_NAME = re.compile(r"(?i)(key|token|secret|passw|cred|auth|sops)")


class UsageError(Exception):
    pass


class Forbidden(NamedTuple):
    env_values: dict          # name -> value, for environment variables with a secret-looking name
    identity_words: list      # the user's name, email and login, which prose here never carries


def local_forbidden(environ=None, git_values=None) -> Forbidden:
    """What this machine knows that a log line must not repeat."""
    environ = os.environ if environ is None else environ
    env_values = {name: value for name, value in environ.items()
                  if SECRET_ENV_NAME.search(name) and len(value) >= 6}
    if git_values is None:
        git_values = []
        for key in ("user.name", "user.email"):
            try:
                proc = subprocess.run(["git", "config", key], capture_output=True, text=True)
            except OSError:
                continue
            if proc.returncode == 0:
                git_values.append(proc.stdout.strip())
    identity_words = [word for word in [*git_values, environ.get("USER", ""), environ.get("LOGNAME", "")]
                      if len(word) >= 4]
    return Forbidden(env_values, identity_words)


def mixed_run_problem(text: str) -> bool:
    for run in MIXED_RUN.findall(text):
        if any(c.isdigit() for c in run) and any(c.isupper() for c in run) and any(c.islower() for c in run):
            return True
    return False


def shared_problems(text: str, forbidden: Forbidden) -> list[str]:
    """Rules for any log line. `text` never contains a sha= field."""
    problems = [f"contains {name}" for name, pattern in LINE_RULES if pattern.search(text)]
    if mixed_run_problem(text):
        problems.append("contains a long mixed-case alphanumeric string")
    for name, value in forbidden.env_values.items():
        if value in text:
            problems.append(f"contains the value of environment variable {name}")
    for word in forbidden.identity_words:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", text, re.I):
            problems.append("contains the user's name, email or login (say 'the user')")
            break
    return problems


def text_problems(text: str) -> list[str]:
    problems = []
    if KEY_VALUE.search(text):
        problems.append("looks like a key=value or key: value pair")
    if SHELL_CHARS.search(text):
        problems.append("contains a shell or code character (backtick, ; < > | backslash, $( ${ &&)")
    if IMPERATIVE_START.search(text):
        problems.append("opens with an imperative; say what happened, not what to do")
    if INJECTION.search(text):
        problems.append("contains wording that reads as an instruction to a reader")
    if SENSITIVE_WORD.search(text) and not STRICT_TEXT.match(text):
        problems.append(f"mentions sops, a secret, a credential or similar: letters, spaces, '.', ',' and '-' only, "
                        f"{STRICT_MAX_TEXT} characters at most, so no value can ride along")
    return problems


def parse_entry(line: str):
    return ENTRY.match(line)


def entry_problems(line: str, forbidden: Forbidden) -> list[str]:
    match = parse_entry(line)
    if not match:
        return ["not an entry: expected 'TS wt=NAME kind=KIND [sha=HEX] [paths=A,B] | text' in printable ASCII, "
                f"text 1-{MAX_TEXT} characters"]
    problems = []
    if match["kind"] not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)}")
    if any(item.startswith("/") or item == "" or ".." in item.split("/") for item in (match["paths"] or "x").split(",")):
        problems.append("paths must be repo-relative, with no '..' and no empty item")
    without_sha = re.sub(r" sha=[0-9a-f]{7,40}", "", line, count=1)
    return problems + shared_problems(without_sha, forbidden) + text_problems(match["text"])


def flag_problems(line: str, forbidden: Forbidden) -> list[str]:
    match = FLAG_ENTRY.match(line)
    if not match:
        return ["not a flag entry: expected 'TS kind=flag|resolved ref=SLUG:LINE | text'"]
    return shared_problems(line, forbidden) + text_problems(match["text"])


def utc_now() -> datetime:
    """Now in UTC, without tzinfo: the entries' timestamps are parsed without one, and the two must subtract."""
    return datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)


def slugify(branch: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", branch.lower()).strip("-.")[:80]


def git_output(*args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True)
    except OSError as err:
        raise UsageError(f"git is not available: {err}") from err
    if proc.returncode != 0:
        raise UsageError("not inside a git repository (or pass --log-dir and --wt)")
    return proc.stdout.strip()


def default_log_dir() -> Path:
    return Path(git_output("rev-parse", "--git-common-dir")).resolve() / "agent-log"


def default_worktree_name() -> str:
    return Path(git_output("rev-parse", "--show-toplevel")).name


def default_archive_dir() -> Path:
    return Path(git_output("rev-parse", "--show-toplevel")) / ARCHIVE_DIR


def read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line for line in path.read_text().splitlines() if line.strip()]


def live_entries(log_dir: Path) -> list[tuple[Path, str]]:
    found = []
    for path in sorted(log_dir.glob("*.log")) if log_dir.is_dir() else []:
        found += [(path, line) for line in read_lines(path)]
    return found


def archive_entries(archive_dir: Path) -> list[tuple[Path, int, str]]:
    found = []
    for path in sorted(archive_dir.glob("*.log")) if archive_dir.is_dir() else []:
        if path.name == FLAGS_FILE:
            continue
        found += [(path, number, line) for number, line in enumerate(path.read_text().splitlines(), 1)]
    return found


def write_atomically(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(handle, "w") as out:
        out.write("".join(line + "\n" for line in lines))
    os.replace(temp_name, path)


def report(problems: list[str], where: str) -> None:
    for problem in problems:
        print(f"{where}: {problem}", file=sys.stderr)


def command_add(args) -> int:
    log_dir = args.log_dir or default_log_dir()
    worktree = args.wt or default_worktree_name()
    now = datetime.strptime(args.now, TS_FORMAT) if args.now else utc_now()
    header = f"{now.strftime(TS_FORMAT)} wt={worktree} kind={args.kind}"
    if args.sha:
        header += f" sha={args.sha}"
    if args.paths:
        header += f" paths={args.paths}"
    line = f"{header} | {' '.join(args.text)}"
    problems = entry_problems(line, local_forbidden())
    if problems:
        report(problems, "rejected, nothing written")
        return 2
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / f"{worktree}.log", "a") as out:
        out.write(line + "\n")
    return 0


def open_claims(lines: list[str]) -> list[str]:
    """Claims with no later release or abandon from the same worktree."""
    parsed = sorted((m for m in map(parse_entry, lines) if m), key=lambda m: m["ts"])
    open_by_worktree: dict[str, list[str]] = {}
    for m in parsed:
        if m["kind"] == "claim":
            open_by_worktree.setdefault(m["wt"], []).append(m.string)
        elif m["kind"] in ("release", "abandon"):
            open_by_worktree.pop(m["wt"], None)
    return sorted((line for lines_ in open_by_worktree.values() for line in lines_),
                  key=lambda line: line.split(" ", 1)[0])


def paths_overlap(entry_paths: str | None, wanted: list[str]) -> bool:
    if not entry_paths:
        return False
    for have in entry_paths.split(","):
        for want in wanted:
            if have == want or have.startswith(want.rstrip("/") + "/") or want.startswith(have.rstrip("/") + "/"):
                return True
    return False


def command_read(args) -> int:
    log_dir = args.log_dir or default_log_dir()
    lines = [line for _, line in live_entries(log_dir)]
    if args.archive:
        archive_dir = args.archive_dir or default_archive_dir()
        lines += [line for _, _, line in archive_entries(archive_dir) if parse_entry(line)]
    now = datetime.strptime(args.now, TS_FORMAT) if args.now else utc_now()
    wanted = args.paths.split(",") if args.paths else []

    def stale_note(line: str) -> str:
        age = now - datetime.strptime(line.split(" ", 1)[0], TS_FORMAT)
        return " [stale]" if age > timedelta(hours=args.stale_hours) else ""

    claims = open_claims(lines)
    if wanted:
        claims = [c for c in claims if paths_overlap(parse_entry(c)["paths"], wanted)]
    print("open claims:" if claims else "open claims: none")
    for claim in claims:
        print(f"  {claim}{stale_note(claim)}")
    if args.claims:
        return 0
    recent = sorted((m for m in map(parse_entry, lines) if m), key=lambda m: m["ts"])
    if wanted:
        recent = [m for m in recent if paths_overlap(m["paths"], wanted)]
    print(f"recent ({min(args.tail, len(recent))}):")
    for m in recent[-args.tail:]:
        print(f"  {m.string}")
    return 0


def command_archive(args) -> int:
    log_dir = args.log_dir or default_log_dir()
    worktrees = args.wt or [default_worktree_name()]
    branch = args.branch or git_output("branch", "--show-current")
    slug = slugify(branch)
    if not slug:
        raise UsageError("no branch name: pass --branch")
    archive_dir = args.archive_dir or default_archive_dir()

    chosen = []
    for path, line in live_entries(log_dir):
        match = parse_entry(line)
        if match and match["wt"] in worktrees:
            chosen.append((path, line))
    forbidden = local_forbidden()
    failed = False
    for path, line in chosen:
        problems = entry_problems(line, forbidden)
        if problems:
            failed = True
            report(problems, f"{path.name}: {line.split(' ', 1)[0]} {line.split(' ')[2]}")
    if failed:
        print("archive stopped: nothing written, nothing removed from the live log", file=sys.stderr)
        return 1

    kept = sorted({line for _, line in chosen if parse_entry(line)["kind"] not in DROPPED_ON_ARCHIVE})
    target = archive_dir / f"{slug}.log"
    existing = read_lines(target)
    new_lines = [line for line in kept if line not in existing]
    print(f"{len(chosen)} live entries for {', '.join(worktrees)}; {len(kept)} kept after compaction, "
          f"{len(new_lines)} new in {target.name}")
    if args.dry_run:
        return 0
    if new_lines:
        write_atomically(target, sorted([*existing, *new_lines]))
    chosen_set = {line for _, line in chosen}
    for path in {path for path, _ in chosen}:
        remaining = [line for line in read_lines(path) if line not in chosen_set]
        if remaining:
            write_atomically(path, remaining)
        else:
            path.unlink()
    return 0


def flag_state(flag_lines: list[str]):
    """(unresolved refs, all parsed flag entries) from the flags file, oldest first."""
    parsed = sorted((m for m in map(FLAG_ENTRY.match, flag_lines) if m), key=lambda m: m["ts"])
    unresolved: dict[str, str] = {}
    for m in parsed:
        ref = f"{m['slug']}:{m['line']}"
        if m["kind"] == "flag":
            unresolved[ref] = m.string
        else:
            unresolved.pop(ref, None)
    return unresolved, parsed


def command_check(args) -> int:
    archive_dir = args.archive_dir or default_archive_dir()
    forbidden = local_forbidden()
    problems_found = 0
    entries = archive_entries(archive_dir)
    for path, number, line in entries:
        problems = entry_problems(line, forbidden)
        if problems:
            problems_found += len(problems)
            report(problems, f"{path.name}:{number}")
    flag_lines = read_lines(archive_dir / FLAGS_FILE)
    for number, line in enumerate(flag_lines, 1):
        problems = flag_problems(line, forbidden)
        if problems:
            problems_found += len(problems)
            report(problems, f"{FLAGS_FILE}:{number}")
    unresolved, parsed = flag_state(flag_lines)
    for ref, line in unresolved.items():
        problems_found += 1
        print(f"unresolved flag {ref}: {line}", file=sys.stderr)
    if args.recent_days is not None:
        now = datetime.strptime(args.now, TS_FORMAT) if args.now else utc_now()
        cutoff = now - timedelta(days=args.recent_days)
        recent = [m for m in parsed if datetime.strptime(m["ts"], TS_FORMAT) >= cutoff]
        print(f"flags in the last {args.recent_days} days: {len(recent)}")
        for m in recent:
            print(f"  {m.string}")
    print(f"agent-log check: {len(entries)} archived lines, {len(unresolved)} unresolved flags, "
          f"{'FAILED' if problems_found else 'ok'}")
    return 1 if problems_found else 0


def archive_line(archive_dir: Path, slug: str, number: int) -> tuple[Path, list[str]]:
    path = archive_dir / f"{slug}.log"
    lines = path.read_text().splitlines() if path.is_file() else []
    if not 1 <= number <= len(lines):
        raise UsageError(f"{path.name} has no line {number}")
    return path, lines


def append_flag_entry(args, kind: str) -> int:
    archive_dir = args.archive_dir or default_archive_dir()
    archive_line(archive_dir, args.slug, args.line)
    now = datetime.strptime(args.now, TS_FORMAT) if args.now else utc_now()
    line = f"{now.strftime(TS_FORMAT)} kind={kind} ref={args.slug}:{args.line} | {' '.join(args.text)}"
    problems = flag_problems(line, local_forbidden())
    if problems:
        report(problems, "rejected, nothing written (describe where, never the value)")
        return 2
    flags_path = archive_dir / FLAGS_FILE
    write_atomically(flags_path, [*read_lines(flags_path), line])
    return 0


def command_flag(args) -> int:
    return append_flag_entry(args, "flag")


def command_resolve(args) -> int:
    archive_dir = args.archive_dir or default_archive_dir()
    path, lines = archive_line(archive_dir, args.slug, args.line)
    if args.redact:
        match = parse_entry(lines[args.line - 1])
        if not match:
            raise UsageError("that line is not a parseable entry; edit it by hand")
        lines[args.line - 1] = lines[args.line - 1][:match.start("text")] + REDACTED_TEXT
        write_atomically(path, lines)
    return append_flag_entry(args, "resolved")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The agent log (see prototypes/agent-log/style-guide.md).")
    parser.add_argument("--log-dir", type=Path, help="live log directory (default: GIT_COMMON_DIR/agent-log)")
    parser.add_argument("--archive-dir", type=Path, help="archive directory (default: REPO/wiki/agent-log)")
    parser.add_argument("--now", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="append one entry to the live log")
    add.add_argument("kind", choices=KINDS)
    add.add_argument("--paths", help="comma-separated repo-relative paths")
    add.add_argument("--sha", help="commit sha, 7-40 lowercase hex")
    add.add_argument("--wt", help="worktree name (default: this worktree's directory name)")
    add.add_argument("text", nargs="+")
    add.set_defaults(run=command_add)

    read = sub.add_parser("read", help="open claims, then the most recent entries")
    read.add_argument("--paths", help="only entries that touch these comma-separated paths")
    read.add_argument("--tail", type=int, default=DEFAULT_TAIL)
    read.add_argument("--claims", action="store_true", help="open claims only")
    read.add_argument("--archive", action="store_true", help="include archived entries")
    read.add_argument("--stale-hours", type=int, default=DEFAULT_STALE_HOURS)
    read.set_defaults(run=command_read)

    archive = sub.add_parser("archive", help="move a branch's entries, compacted, into the archive")
    archive.add_argument("--branch", help="branch name; names the archive file (default: current branch)")
    archive.add_argument("--wt", action="append", help="worktree whose entries to archive (repeatable)")
    archive.add_argument("--dry-run", action="store_true")
    archive.set_defaults(run=command_archive)

    check = sub.add_parser("check", help="re-run the current rules over the archive and list unresolved flags")
    check.add_argument("--recent-days", type=int, help="also list flags and resolutions from the last N days")
    check.set_defaults(run=command_check)

    for name, help_text, run in (("flag", "record that a secret or instruction got into an archived line", command_flag),
                                 ("resolve", "record what was done about a flagged line", command_resolve)):
        flagging = sub.add_parser(name, help=help_text)
        flagging.add_argument("slug")
        flagging.add_argument("line", type=int)
        flagging.add_argument("text", nargs="+", help="where and what kind; never the value itself")
        if name == "resolve":
            flagging.add_argument("--redact", action="store_true", help="overwrite the line's text in the archive")
        flagging.set_defaults(run=run)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except UsageError as err:
        print(f"agent_log: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
