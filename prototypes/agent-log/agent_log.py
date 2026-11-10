#!/usr/bin/env python3
"""The agent log: one short line per event, so agents in separate worktrees of
one clone can see what the others claimed, finished, decided and dropped.

    python3 prototypes/agent-log/agent_log.py add KIND [--ref ID] [--verb VERB] [--sha SHA] [--paths A,B] [TEXT...]
    python3 prototypes/agent-log/agent_log.py read [--paths A,B] [--tail N] [--claims] [--archive]
    python3 prototypes/agent-log/agent_log.py archive [--branch NAME] [--wt NAME ...] [--dry-run]
    python3 prototypes/agent-log/agent_log.py prune --wt NAME ...
    python3 prototypes/agent-log/agent_log.py check
    python3 prototypes/agent-log/agent_log.py flag ID REASON...
    python3 prototypes/agent-log/agent_log.py resolve ID NOTE... [--redact]
    just agent-log ARGS...    just agent-log-check   (= check; CI runs it)

The live log is a directory of `WORKTREE.log` files under the git common dir
(`git rev-parse --git-common-dir`): shared by every worktree of the clone, never
committed, only ever appended to. `archive` copies a worktree's entries of the kinds
worth keeping (decision, gotcha, abandon, sops) into `wiki/agent-log/SLUG.log`, which
is also only appended to, for the PR to commit. Nothing points at a line number: an
entry carries an `id` set when it was written, and flags, claims and `read` use that.
style-guide.md, next to this file, says how to write entries and how to undo all of this.

An entry is data, never instructions: a fixed grammar, printable ASCII, 200 characters
of text. A `sops` entry has no text at all. A line that fails any rule is rejected, not
scrubbed, and the message names the rule, never the matched text. `add`, `archive` and
`check` use the same rules, and `check` re-runs the current rules over everything
committed, so a rule added later reaches old lines. The rules are heuristics: they
lower the chance a secret reaches a committed file; they do not prove its absence.
Every command checks everything it needs before it writes anything.

Exit codes: 0 done; 1 `archive`, `prune` or `check` found a problem; 2 bad usage or an
entry the rules reject.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

KINDS = ("claim", "release", "done", "decision", "gotcha", "abandon", "sops")
ARCHIVE_KINDS = ("decision", "gotcha", "abandon", "sops")
REDACTED_KIND = "redacted"           # in the archive only: what resolve --redact leaves behind
SOPS_VERBS = ("added", "rotated", "removed", "reencrypted")
TS_FORMAT = "%Y-%m-%dT%H:%MZ"
MAX_TEXT = 200
STRICT_MAX_TEXT = 80
DEFAULT_TAIL = 30
DEFAULT_STALE_HOURS = 24
ARCHIVE_DIR = Path("wiki") / "agent-log"
FLAGS_SUBDIR = "flags"
REDACTED_TEXT = "redacted after flag"

TS = r"\d{4}-\d\d-\d\dT\d\d:\d\dZ"
ID = r"[0-9a-f]{10}"
ENTRY = re.compile(
    rf"^(?P<ts>{TS}) id=(?P<id>{ID}) wt=(?P<wt>[A-Za-z0-9._-]{{1,60}}) kind=(?P<kind>[a-z]+)"
    rf"(?: ref=(?P<ref>{ID}))?"
    r"(?: verb=(?P<verb>[a-z]+))?"
    r"(?: sha=(?P<sha>[0-9a-f]{7,40}))?"
    r"(?: paths=(?P<paths>[A-Za-z0-9._/@+,-]{1,300}))?"
    rf"(?: \| (?P<text>[ -~]{{1,{MAX_TEXT}}}))?$"
)
FLAG_ENTRY = re.compile(
    rf"^(?P<ts>{TS}) kind=(?P<kind>flag|resolved) ref=(?P<ref>{ID}) \| (?P<text>[ -~]{{1,{MAX_TEXT}}})$"
)
FLAG_FILE_NAME = re.compile(rf"^(?P<id>{ID})\.(?P<state>flag|resolved)$")
ID_ONLY = re.compile(rf"^{ID}$")

# Rules on the whole line. Each is (name shown to the writer, pattern).
LINE_RULES = [
    ("a token or key prefix", re.compile(
        r"ghp_|gho_|ghs_|ghu_|github_pat_|glpat-|npm_[A-Za-z0-9]|\bsk-|sk_live|rk_live|pk_live|\bAKIA|\bASIA[A-Z0-9]"
        r"|xox[abeprs]-|ya29\.|\bAIza|-----BEGIN|\beyJ[A-Za-z0-9_-]{8,}")),
    ("sops ciphertext or an age key", re.compile(r"ENC\[|AGE-SECRET-KEY-|\bage1[0-9a-z]{20,}")),
    ("a long hex string (a sha goes in sha=)", re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{16,}(?![0-9A-Za-z])")),
    ("a UUID", re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")),
    ("a URL with credentials", re.compile(r"://[^/\s]*@")),
    ("a home directory path", re.compile(r"/home/|/Users/|/root/|[A-Za-z]:\\Users\\")),
]
ALNUM_RUN = re.compile(r"[A-Za-z0-9+/=]{24,}")

# Rules on the text field only.
KEY_VALUE = re.compile(r"(?i)\b\w*(key|token|secret|passw|credential|auth|bearer)\w*\s*[=:]\s*\S")
CODE_CHARS = re.compile(r"[`]|\$\(|\$\{")
INJECTION = re.compile(
    r"(?i)(ignore (all |any |previous|prior|the above)|disregard|system prompt|instructions? (say|from|above)"
    r"|as an ai|you are now|new instructions?)")
SENSITIVE_WORD = re.compile(
    r"(?i)\b(sops|secrets?|vault|credentials?|keyring|passwords?|passwd|passphrases?|pw|tokens?|keys?|api|pgp|gpg)\b")
STRICT_TEXT = re.compile(rf"^[A-Za-z .,-]{{1,{STRICT_MAX_TEXT}}}$")
SECRET_ENV_NAME = re.compile(r"(?i)(key|token|secret|passw|cred|auth|sops)")


class UsageError(Exception):
    pass


class Forbidden(NamedTuple):
    env_values: dict          # name -> value, for environment variables with a secret-looking name
    identity_words: list      # the git user's name and email, which prose here never carries


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
    return Forbidden(env_values, [word for word in git_values if len(word) >= 4])


def alnum_run_problem(text: str) -> bool:
    for run in ALNUM_RUN.findall(text):
        has_digit = any(c.isdigit() for c in run)
        has_letter = any(c.isalpha() for c in run)
        mixed_case = any(c.isupper() for c in run) and any(c.islower() for c in run)
        if has_digit and has_letter and (mixed_case or len(run) >= 32):
            return True
    return False


def shared_problems(text: str, forbidden: Forbidden) -> list[str]:
    """Rules for the whole line, with the id and sha fields already removed."""
    problems = [f"contains {name}" for name, pattern in LINE_RULES if pattern.search(text)]
    if alnum_run_problem(text):
        problems.append("contains a long alphanumeric string that looks like a key")
    for name, value in forbidden.env_values.items():
        if value in text:
            problems.append(f"contains the value of environment variable {name}")
    for word in forbidden.identity_words:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", text, re.I):
            problems.append("contains the git user's name or email (say 'the user')")
            break
    return problems


def text_problems(text: str) -> list[str]:
    problems = []
    if KEY_VALUE.search(text):
        problems.append("looks like a key=value or key: value pair")
    if CODE_CHARS.search(text):
        problems.append("contains a backtick, $( or ${")
    if INJECTION.search(text):
        problems.append("contains wording that reads as an instruction to a reader")
    if SENSITIVE_WORD.search(text) and not STRICT_TEXT.match(text):
        problems.append(f"mentions sops, a secret, a key, a password or similar: letters, spaces, '.', ',' and '-' "
                        f"only, {STRICT_MAX_TEXT} characters at most, so no value can ride along")
    return problems


def parse_entry(line: str):
    return ENTRY.match(line)


def entry_body(line: str) -> str:
    """The line without its id field: what the id is the hash of."""
    return re.sub(rf" id={ID}", "", line, count=1)


def entry_id(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:10]   # utf-8: a non-ASCII line is rejected by the grammar, not by a crash here


def shape_problems(match, stored: bool) -> list[str]:
    kind, verb, ref, text, paths = (match[name] for name in ("kind", "verb", "ref", "text", "paths"))
    problems = []
    if kind == REDACTED_KIND:
        return [] if stored else ["kind redacted is only written by resolve --redact"]
    if kind not in KINDS:
        return [f"kind must be one of {', '.join(KINDS)}"]
    if kind == "sops":
        if verb not in SOPS_VERBS:
            problems.append(f"a sops entry needs verb= one of {', '.join(SOPS_VERBS)}")
        if text is not None:
            problems.append("a sops entry has no text")
        if not paths:
            problems.append("a sops entry needs paths= naming the file")
    else:
        if verb is not None:
            problems.append("verb= belongs to sops entries only")
        if text is None:
            problems.append("an entry needs text after ' | '")
    if ref is not None and kind not in ("release", "abandon"):
        problems.append("ref= belongs to release and abandon entries only")
    if paths is not None and any(item == "" or item.startswith("/") or ".." in item.split("/")
                                 for item in paths.split(",")):
        problems.append("paths must be repo-relative, with no '..' and no empty item")
    return problems


def entry_problems(line: str, forbidden: Forbidden, stored: bool = False) -> list[str]:
    match = parse_entry(line)
    if not match:
        return ["not an entry: expected 'TS id=HEX10 wt=NAME kind=KIND [ref=ID] [verb=V] [sha=HEX] [paths=A,B] "
                f"[| text]' in printable ASCII, text 1-{MAX_TEXT} characters"]
    problems = []
    try:
        datetime.strptime(match["ts"], TS_FORMAT)
    except ValueError:
        problems.append("timestamp is not a real date and time")
    problems += shape_problems(match, stored)
    if match["kind"] != REDACTED_KIND and entry_id(entry_body(line)) != match["id"]:
        problems.append("id does not match the line's content")
    stripped = re.sub(r" sha=[0-9a-f]{7,40}", "", entry_body(line), count=1)
    problems += shared_problems(stripped, forbidden)
    if match["text"] is not None:
        problems += text_problems(match["text"])
    return problems


def flag_problems(line: str, forbidden: Forbidden) -> list[str]:
    match = FLAG_ENTRY.match(line)
    if not match:
        return ["not a flag entry: expected 'TS kind=flag|resolved ref=ID | text'"]
    return shared_problems(line, forbidden) + text_problems(match["text"])


def utc_now() -> datetime:
    """Now in UTC, without tzinfo: the entries' timestamps are parsed without one, and the two must subtract."""
    return datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)


def now_from(args) -> datetime:
    if not args.now:
        return utc_now()
    try:
        return datetime.strptime(args.now, TS_FORMAT)
    except ValueError as err:
        raise UsageError(f"--now must look like 2026-11-09T14:02Z ({err})") from err


def slugify(branch: str) -> str:
    return re.sub(r"[^a-z0-9._-]+", "-", branch.lower()).strip("-.")[:80]


def git_output(*args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True)
    except OSError as err:
        raise UsageError(f"git is not available: {err}") from err
    if proc.returncode != 0:
        raise UsageError("not inside a git repository (or pass --log-dir, --archive-dir, --repo-root and --wt)")
    return proc.stdout.strip()


def default_log_dir() -> Path:
    return Path(git_output("rev-parse", "--git-common-dir")).resolve() / "agent-log"


def default_repo_root() -> Path:
    return Path(git_output("rev-parse", "--show-toplevel"))


def default_worktree_name() -> str:
    return default_repo_root().name


def archive_dir_of(args) -> Path:
    return args.archive_dir or (args.repo_root or default_repo_root()) / ARCHIVE_DIR


def read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line for line in path.read_text().splitlines() if line.strip()]


def live_files(log_dir: Path) -> list[Path]:
    return sorted(log_dir.glob("*.log")) if log_dir.is_dir() else []


def archive_files(archive_dir: Path) -> list[Path]:
    return sorted(archive_dir.glob("*.log")) if archive_dir.is_dir() else []


def archived_ids(archive_dir: Path) -> set[str]:
    ids = set()
    for path in archive_files(archive_dir):
        for line in read_lines(path):
            match = parse_entry(line)
            if match:
                ids.add(match["id"])
    return ids


def append_text(path: Path, text: str) -> None:
    """One O_APPEND write: concurrent writers interleave whole writes, never halves of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(handle, text.encode("ascii"))
    finally:
        os.close(handle)


def create_new_file(path: Path, text: str) -> bool:
    """Write a file that must not exist yet; False if it already does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    try:
        os.write(handle, text.encode("ascii"))
    finally:
        os.close(handle)
    return True


def report(problems: list[str], where: str) -> None:
    for problem in problems:
        print(f"{where}: {problem}", file=sys.stderr)


def known_paths(repo_root: Path) -> set[str]:
    """Files at HEAD, so a path that was just deleted can still be named. Empty without git."""
    try:
        proc = subprocess.run(["git", "-C", str(repo_root), "ls-tree", "-r", "--name-only", "HEAD"],
                              capture_output=True, text=True)
    except OSError:
        return set()
    return set(proc.stdout.splitlines()) if proc.returncode == 0 else set()


def missing_path_problems(paths: str | None, repo_root: Path) -> list[str]:
    """Paths in an entry that will be committed must name something that exists or existed at HEAD."""
    if not paths:
        return []
    at_head = known_paths(repo_root)
    problems = []
    for item in paths.split(","):
        on_disk = (repo_root / item).exists()
        in_tree = item in at_head or any(name.startswith(item.rstrip("/") + "/") for name in at_head)
        if not (on_disk or in_tree):
            problems.append("a path in an archived entry must exist in the repo or at HEAD")
            break
    return problems


def command_add(args) -> int:
    log_dir = args.log_dir or default_log_dir()
    worktree = args.wt or default_worktree_name()
    repo_root = args.repo_root or (default_repo_root() if args.kind in ARCHIVE_KINDS and args.paths else Path("."))
    now = now_from(args)
    body = f"{now.strftime(TS_FORMAT)} wt={worktree} kind={args.kind}"
    for field, value in (("ref", args.ref), ("verb", args.verb), ("sha", args.sha), ("paths", args.paths)):
        if value:
            body += f" {field}={value}"
    if args.text:
        body += f" | {' '.join(args.text)}"
    line = body.replace(f" wt={worktree}", f" id={entry_id(body)} wt={worktree}", 1)
    problems = entry_problems(line, local_forbidden())
    if not problems and args.kind in ARCHIVE_KINDS:
        problems = missing_path_problems(args.paths, repo_root)
    if problems:
        report(problems, "rejected, nothing written")
        return 2
    append_text(log_dir / f"{worktree}.log", line + "\n")
    print(parse_entry(line)["id"])
    return 0


def open_claims(lines: list[str]) -> list[str]:
    """Claims not closed by a release naming them, a release with no ref, or an abandon from the same worktree."""
    parsed = sorted((m for m in map(parse_entry, lines) if m), key=lambda m: m["ts"])
    open_by_worktree: dict[str, dict[str, str]] = {}
    for m in parsed:
        claims = open_by_worktree.setdefault(m["wt"], {})
        if m["kind"] == "claim":
            claims[m["id"]] = m.string
        elif m["kind"] == "release" and m["ref"]:
            claims.pop(m["ref"], None)
        elif m["kind"] in ("release", "abandon"):
            claims.clear()
    return sorted((line for claims in open_by_worktree.values() for line in claims.values()),
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
    archive_dir = archive_dir_of(args)
    lines = [line for path in live_files(log_dir) for line in read_lines(path)]
    if args.archive:
        lines += [line for path in archive_files(archive_dir) for line in read_lines(path)]
    else:
        already_archived = archived_ids(archive_dir)
        lines = [line for line in lines if (parse_entry(line) or {"id": None})["id"] not in already_archived]
    now = now_from(args)
    wanted = args.paths.split(",") if args.paths else []

    def stale_note(line: str) -> str:
        age = now - datetime.strptime(parse_entry(line)["ts"], TS_FORMAT)
        return " [stale]" if age > timedelta(hours=args.stale_hours) else ""

    readable = []
    for line in lines:
        match = parse_entry(line)
        try:
            if match:
                datetime.strptime(match["ts"], TS_FORMAT)
                readable.append(line)
        except ValueError:
            continue
    claims = open_claims(readable)
    if wanted:
        claims = [c for c in claims if paths_overlap(parse_entry(c)["paths"], wanted)]
    print("open claims:" if claims else "open claims: none")
    for claim in claims:
        print(f"  {claim}{stale_note(claim)}")
    if args.claims:
        return 0
    recent = sorted((parse_entry(line) for line in readable), key=lambda m: m["ts"])
    if wanted:
        recent = [m for m in recent if paths_overlap(m["paths"], wanted)]
    shown = recent[-args.tail:] if args.tail > 0 else []
    older = len(recent) - len(shown)
    print(f"recent ({len(shown)}{f', {older} older not shown' if older else ''}):")
    for m in shown:
        print(f"  {m.string}")
    return 0


def command_archive(args) -> int:
    log_dir = args.log_dir or default_log_dir()
    worktrees = args.wt or [default_worktree_name()]
    branch = args.branch or git_output("branch", "--show-current")
    slug = slugify(branch)
    if not slug:
        raise UsageError("no branch name: pass --branch")
    archive_dir = archive_dir_of(args)
    repo_root = args.repo_root or default_repo_root()

    chosen = []
    for path in live_files(log_dir):
        for line in read_lines(path):
            match = parse_entry(line)
            if match and match["wt"] in worktrees and match["kind"] in ARCHIVE_KINDS:
                chosen.append(line)
    forbidden = local_forbidden()
    failed = False
    for line in chosen:
        match = parse_entry(line)
        problems = entry_problems(line, forbidden) + missing_path_problems(match["paths"], repo_root)
        if problems:
            failed = True
            report(problems, f"entry id={match['id']} kind={match['kind']}")
    if failed:
        print("archive stopped: nothing written", file=sys.stderr)
        return 1

    present = archived_ids(archive_dir)
    new_lines = []
    for line in sorted(chosen, key=lambda line: line.split(" ", 1)[0]):
        entry_id_ = parse_entry(line)["id"]
        if entry_id_ not in present:
            present.add(entry_id_)
            new_lines.append(line)
    target = archive_dir / f"{slug}.log"
    print(f"{len(chosen)} entries of kinds {', '.join(ARCHIVE_KINDS)} for {', '.join(worktrees)}; "
          f"{len(new_lines)} new for {target.name}")
    if new_lines and not args.dry_run:
        append_text(target, "".join(line + "\n" for line in new_lines))
    return 0


def command_prune(args) -> int:
    log_dir = args.log_dir or default_log_dir()
    archive_dir = archive_dir_of(args)
    present = archived_ids(archive_dir)
    removable = []
    refused = False
    for name in args.wt:
        path = log_dir / f"{name}.log"
        if not path.is_file():
            print(f"prune: no live log for {name}", file=sys.stderr)
            refused = True
            continue
        unarchived = []
        for line in read_lines(path):
            match = parse_entry(line)
            if not match:
                unarchived.append("a line that is not an entry")
            elif match["kind"] in ARCHIVE_KINDS and match["id"] not in present:
                unarchived.append(f"id={match['id']} kind={match['kind']}")
        if unarchived:
            refused = True
            print(f"prune: {name} still has {len(unarchived)} entries not in the archive "
                  f"(first: {unarchived[0]}); archive them or write them off by hand", file=sys.stderr)
        else:
            removable.append(path)
    if refused:
        print("prune stopped: nothing removed", file=sys.stderr)
        return 1
    for path in removable:
        path.unlink()
        print(f"removed {path.name}")
    return 0


def flag_dir_of(args) -> Path:
    return (archive_dir_of(args)) / FLAGS_SUBDIR


def checked_id(value: str) -> str:
    if not ID_ONLY.match(value):
        raise UsageError("an entry id is 10 lowercase hex characters")
    return value


def find_archived_line(archive_dir: Path, wanted_id: str):
    """(path, index in that file's lines, line) of the archived entry with this id, or None."""
    for path in archive_files(archive_dir):
        for index, line in enumerate(path.read_text().splitlines()):
            match = parse_entry(line)
            if match and match["id"] == wanted_id:
                return path, index, line
    return None


def command_flag(args) -> int:
    archive_dir = archive_dir_of(args)
    wanted_id = checked_id(args.id)
    if not find_archived_line(archive_dir, wanted_id):
        raise UsageError("no archived entry has that id")
    line = f"{now_from(args).strftime(TS_FORMAT)} kind=flag ref={wanted_id} | {' '.join(args.text)}"
    problems = flag_problems(line, local_forbidden())
    if problems:
        report(problems, "rejected, nothing written (describe where, never the value)")
        return 2
    if not create_new_file(flag_dir_of(args) / f"{wanted_id}.flag", line + "\n"):
        raise UsageError("that entry is already flagged")
    return 0


def command_resolve(args) -> int:
    archive_dir = archive_dir_of(args)
    wanted_id = checked_id(args.id)
    flags = flag_dir_of(args)
    if not (flags / f"{wanted_id}.flag").is_file():
        raise UsageError("that entry is not flagged")
    if (flags / f"{wanted_id}.resolved").exists():
        raise UsageError("that flag is already resolved")
    line = f"{now_from(args).strftime(TS_FORMAT)} kind=resolved ref={wanted_id} | {' '.join(args.text)}"
    problems = flag_problems(line, local_forbidden())
    if problems:
        report(problems, "rejected, nothing written (describe what was done, never the value)")
        return 2
    redacted_file = None
    if args.redact:
        found = find_archived_line(archive_dir, wanted_id)
        if not found:
            raise UsageError("no archived entry has that id")
        path, index, archived = found
        match = parse_entry(archived)
        lines = path.read_text().splitlines()
        lines[index] = (f"{match['ts']} id={wanted_id} wt={match['wt']} kind={REDACTED_KIND} | {REDACTED_TEXT}")
        handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        with os.fdopen(handle, "w") as out:
            out.write("".join(text + "\n" for text in lines))
        redacted_file = (path, temp_name)
    if not create_new_file(flags / f"{wanted_id}.resolved", line + "\n"):
        if redacted_file:
            os.unlink(redacted_file[1])
        raise UsageError("that flag is already resolved")
    if redacted_file:
        os.replace(redacted_file[1], redacted_file[0])
    return 0


def command_check(args) -> int:
    archive_dir = archive_dir_of(args)
    forbidden = local_forbidden()
    problems_found = 0
    count = 0
    for path in archive_files(archive_dir):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            count += 1
            problems = entry_problems(line, forbidden, stored=True)
            if problems:
                problems_found += len(problems)
                report(problems, f"{path.name}:{number}")
    flags = archive_dir / FLAGS_SUBDIR
    states: dict[str, set[str]] = {}
    for path in sorted(flags.glob("*")) if flags.is_dir() else []:
        named = FLAG_FILE_NAME.match(path.name)
        if not named:
            problems_found += 1
            print(f"{FLAGS_SUBDIR}/{path.name}: not a flag file name (ID.flag or ID.resolved)", file=sys.stderr)
            continue
        states.setdefault(named["id"], set()).add(named["state"])
        for line in read_lines(path):
            problems = flag_problems(line, forbidden)
            if problems:
                problems_found += len(problems)
                report(problems, f"{FLAGS_SUBDIR}/{path.name}")
    unresolved = sorted(flag_id for flag_id, seen in states.items() if "flag" in seen and "resolved" not in seen)
    for flag_id in unresolved:
        problems_found += 1
        print(f"unresolved flag on entry {flag_id}: {FLAGS_SUBDIR}/{flag_id}.flag", file=sys.stderr)
    print(f"agent-log check: {count} archived lines, {len(unresolved)} unresolved flags, "
          f"{'FAILED' if problems_found else 'ok'}")
    return 1 if problems_found else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The agent log (see style-guide.md next to this file).")
    parser.add_argument("--log-dir", type=Path, help="live log directory (default: GIT_COMMON_DIR/agent-log)")
    parser.add_argument("--archive-dir", type=Path,
                        help="archive directory (default: REPO_ROOT/wiki/agent-log)")
    parser.add_argument("--repo-root", type=Path, help="where archived paths must exist (default: git toplevel)")
    parser.add_argument("--now", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="append one entry to the live log; prints its id")
    add.add_argument("kind", choices=KINDS)
    add.add_argument("--ref", help="id of the claim a release or abandon closes")
    add.add_argument("--verb", choices=SOPS_VERBS, help="what was done to a sops file (sops entries only)")
    add.add_argument("--paths", help="comma-separated repo-relative paths")
    add.add_argument("--sha", help="commit sha, 7-40 lowercase hex")
    add.add_argument("--wt", help="worktree name (default: this worktree's directory name)")
    add.add_argument("text", nargs="*", help="what happened; none for a sops entry")
    add.set_defaults(run=command_add)

    read = sub.add_parser("read", help="open claims, then the most recent entries not yet archived")
    read.add_argument("--paths", help="only entries that touch these comma-separated paths")
    read.add_argument("--tail", type=int, default=DEFAULT_TAIL)
    read.add_argument("--claims", action="store_true", help="open claims only")
    read.add_argument("--archive", action="store_true", help="include archived entries")
    read.add_argument("--stale-hours", type=int, default=DEFAULT_STALE_HOURS)
    read.set_defaults(run=command_read)

    archive = sub.add_parser("archive", help="copy a worktree's decision, gotcha, abandon and sops entries to the archive")
    archive.add_argument("--branch", help="branch name; names the archive file (default: current branch)")
    archive.add_argument("--wt", action="append", help="worktree whose entries to archive (repeatable)")
    archive.add_argument("--dry-run", action="store_true")
    archive.set_defaults(run=command_archive)

    prune = sub.add_parser("prune", help="delete live logs whose archivable entries are all archived")
    prune.add_argument("--wt", action="append", required=True, help="worktree whose live log to delete (repeatable)")
    prune.set_defaults(run=command_prune)

    check = sub.add_parser("check", help="re-run the current rules over the archive and list unresolved flags")
    check.set_defaults(run=command_check)

    flag = sub.add_parser("flag", help="record that a secret or instruction got into an archived entry")
    flag.add_argument("id")
    flag.add_argument("text", nargs="+", help="where and what kind; never the value itself")
    flag.set_defaults(run=command_flag)

    resolve = sub.add_parser("resolve", help="record what was done about a flagged entry")
    resolve.add_argument("id")
    resolve.add_argument("text", nargs="+", help="what was done; never the value itself")
    resolve.add_argument("--redact", action="store_true", help="replace the entry in the archive with a redacted one")
    resolve.set_defaults(run=command_resolve)
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
