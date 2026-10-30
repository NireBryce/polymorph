"""Load and validate harness mapping files (TOML, schema = 1)."""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .canon import CLASSES, EVENTS
from .validate import DELIVERIES, direct_errors

SCHEMA = 1
BUILTIN_DIR = Path(__file__).resolve().parents[2] / "mappings"
REPO_DIR = ".agents/polymorph/mappings"

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
    if delivery == "direct":
        bad = direct_errors(raw)
        if bad:
            raise MappingError(f"{source}: " + "; ".join(bad))
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
    m = parse_mapping(raw, str(path))
    if m.name != path.stem:
        raise MappingError(f"{path}: name {m.name!r} does not match filename")
    return m


def load_mapping(
    name: str,
    repo: str | Path = ".",
    mappings_dir: str | Path | None = None,
    trust_dirs: tuple[str | Path, ...] = (),
    warn=None,
) -> Mapping:
    """Find NAME.toml. Order: explicit mappings_dir (must be trusted), else
    repo .agents/polymorph/mappings (overrides built-in, with a warning), else built-in."""
    warn = warn or (lambda msg: print(f"warning: {msg}", file=sys.stderr))
    if not _NAME.match(name):
        raise MappingError(f"invalid harness name {name!r}")
    repo_dir = (Path(repo) / REPO_DIR).resolve()
    builtin = BUILTIN_DIR.resolve()

    if mappings_dir is not None:
        d = Path(mappings_dir).resolve()
        trusted = [repo_dir, builtin, *(Path(t).resolve() for t in trust_dirs)]
        if d not in trusted:
            raise MappingError(
                f"mappings dir {d} is outside the repo's {REPO_DIR}/ and the built-in "
                "mappings; pass --trust-dir to load it"
            )
        f = d / f"{name}.toml"
        if not f.is_file():
            raise MappingError(f"no mapping {name!r} in {d}")
        return _read(f)

    local, built = repo_dir / f"{name}.toml", builtin / f"{name}.toml"
    if local.is_file():
        if built.is_file():
            warn(f"{local} overrides the built-in {name!r} mapping")
        return _read(local)
    if built.is_file():
        return _read(built)
    raise MappingError(f"no mapping named {name!r} (looked in {repo_dir} and {builtin})")
