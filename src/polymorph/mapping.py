"""Load and validate harness mapping files (TOML, schema = 1)."""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .canon import CLASSES, EVENTS
from .resources import MAPPINGS_DIR
from .validate import DELIVERIES, validate_mapping

SCHEMA = 1
BUILTIN_DIR = MAPPINGS_DIR
REPO_DIR = ".polymorph/basecamp/mappings"

TOP_KEYS = {
    "schema", "name", "verified", "config_path", "format", "template",
    "context_file", "skills_dir", "tools", "events", "capabilities",
    "payload_in", "payload_out", "delivery",
}
CAP_KEYS = {"ask", "post_event", "exit_2_blocks", "rewrites_input", "json_answers"}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class MappingError(Exception):
    pass


@dataclass(frozen=True)
class Mapping:
    name: str
    config_path: str
    format: str
    template: str
    verified: str | None = None
    context_file: str | None = None
    skills_dir: str | None = None
    tools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    events: dict[str, str] = field(default_factory=dict)
    capabilities: dict[str, bool] = field(default_factory=dict)
    payload_in: dict = field(default_factory=dict)
    payload_out: dict = field(default_factory=dict)
    delivery: str = "adapter"
    source: str = ""


def _unknown(where: str, got, allowed) -> None:
    bad = sorted(set(got) - set(allowed))
    if bad:
        raise MappingError(f"{where}: unknown key(s) {', '.join(map(repr, bad))}")


def parse_mapping(raw: dict, source: str = "") -> Mapping:
    # One rule for generate, validate and check: whatever validate_mapping rejects
    # (including the required tables) is not loadable.
    errs = validate_mapping(raw)
    if errs:
        raise MappingError(f"{source or 'mapping'}: " + "; ".join(errs))
    _unknown(source or "mapping", raw, TOP_KEYS)
    if raw.get("schema") != SCHEMA:
        raise MappingError(f"{source}: schema must be {SCHEMA}, got {raw.get('schema')!r}")
    for req in ("name", "config_path", "format", "template"):
        if not isinstance(raw.get(req), str):
            raise MappingError(f"{source}: missing or non-string {req!r}")
    tools = raw.get("tools", {})
    _unknown(f"{source} [tools]", tools, CLASSES)
    tools = {k: (v,) if isinstance(v, str) else tuple(v) for k, v in tools.items()}
    events = raw.get("events", {})
    _unknown(f"{source} [events]", events, EVENTS)
    caps = raw.get("capabilities", {})
    _unknown(f"{source} [capabilities]", caps, CAP_KEYS)
    for k, v in caps.items():
        if not isinstance(v, bool):
            raise MappingError(f"{source} [capabilities].{k}: must be a boolean")
    for k, v in events.items():
        if not isinstance(v, str):
            raise MappingError(f"{source} [events].{k}: must be a string")
    delivery = raw.get("delivery", "adapter")
    if delivery not in DELIVERIES:
        raise MappingError(f"{source}: delivery must be one of {', '.join(DELIVERIES)}, got {delivery!r}")
    return Mapping(
        name=raw["name"], config_path=raw["config_path"], format=raw["format"],
        template=raw["template"], verified=raw.get("verified"),
        context_file=raw.get("context_file"), skills_dir=raw.get("skills_dir"),
        tools=tools, events=dict(events), capabilities=dict(caps),
        payload_in=dict(raw.get("payload_in", {})),
        payload_out=dict(raw.get("payload_out", {})), delivery=delivery, source=source,
    )


def _read(path: Path) -> Mapping:
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise MappingError(f"{path}: invalid TOML: {e}") from e
    mapping = parse_mapping(raw, str(path))
    if mapping.name != path.stem:
        raise MappingError(f"{path}: name {mapping.name!r} does not match filename")
    return mapping


def load_mapping(
    name: str,
    repo: str | Path = ".",
    mappings_dir: str | Path | None = None,
    trust_dirs: tuple[str | Path, ...] = (),
    warn=None,
) -> Mapping:
    """Find NAME.toml. Order: explicit mappings_dir (must be trusted), else
    repo .polymorph/basecamp/mappings (overrides built-in, with a warning), else built-in."""
    warn = warn or (lambda msg: print(f"warning: {msg}", file=sys.stderr))
    if not _NAME.match(name):
        raise MappingError(f"invalid harness name {name!r}")
    repo_dir = (Path(repo) / REPO_DIR).resolve()
    builtin = BUILTIN_DIR.resolve()

    if mappings_dir is not None:
        resolved_dir = Path(mappings_dir).resolve()
        trusted = [repo_dir, builtin, *(Path(trust_dir).resolve() for trust_dir in trust_dirs)]
        if resolved_dir not in trusted:
            raise MappingError(
                f"mappings dir {resolved_dir} is outside the repo's {REPO_DIR}/ and the built-in "
                "mappings; pass --trust-dir to load it"
            )
        candidate = resolved_dir / f"{name}.toml"
        if not candidate.is_file():
            raise MappingError(f"no mapping {name!r} in {resolved_dir}")
        return _read(candidate)

    local_path, builtin_path = repo_dir / f"{name}.toml", builtin / f"{name}.toml"
    if local_path.is_file():
        if builtin_path.is_file():
            warn(f"{local_path} overrides the built-in {name!r} mapping")
        return _read(local_path)
    if builtin_path.is_file():
        return _read(builtin_path)
    raise MappingError(f"no mapping named {name!r} (looked in {repo_dir} and {builtin})")
