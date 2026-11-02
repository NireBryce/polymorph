"""equip: the onboarding run -- one command, one loadout.

Slice 3 of the loadout pivot (docs/plan-loadout-pivot.md). Composes the
finished parts: `generate`'s config files, the inventory's gap report, and
the env contract, then records what was issued in a provenance manifest so
edits and staleness are checkable later. Posture-aware: a committed basecamp
gets committed equipped artifacts; otherwise the artifacts are regenerable
and equip offers a marked .gitignore section.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import links as linkmod
from .generate import generate
from .inventory import Coverage, Gap, load_coverage, read_inventory
from .links import Link

MANIFEST = ".polymorph/basecamp/loadout.toml"
GITIGNORE_MARK = "# polymorph equip: issued artifacts below are regenerable (do not edit)"


class EquipError(ValueError):
    """Raised when equip cannot run."""


@dataclass(frozen=True)
class Artifact:
    """One file the loadout issued, with its provenance.

    mapping_path/mapping_hash pin the bytes of the mapping the artifact was
    generated from: the adapter re-reads the mapping at run time, so a swap
    after equip would otherwise be invisible. Entries written before the pin
    (and links) have neither.
    """

    harness: str
    path: str
    hash: str
    source: str
    mapping_path: str | None = None
    mapping_hash: str | None = None


@dataclass
class EquipResult:
    harness: str
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    not_written: list[str] = field(default_factory=list)   # undecided decisions
    undecided: int = 0
    honoured: list[tuple[str, str]] = field(default_factory=list)  # kind, where
    gaps: list[Gap] = field(default_factory=list)
    coverage_known: bool = True
    env_lines: list[str] | None = None                     # None: no env.toml, or an invalid one
    env_error: str = ""                                    # why env.toml could not be used, if it exists
    linked: list[Link] = field(default_factory=list)       # symlinks made (dry run: that would be made)
    link_refused: list[tuple[str, str, str]] = field(default_factory=list)   # harness, path, reason
    manifest_written: bool = False
    posture: str = "unknown"                               # maintainer | contributor | unknown
    gitignore_suggested: list[str] = field(default_factory=list)
    gitignore_appended: bool = False
    dry_run: bool = False


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _posture(repo: Path) -> str:
    """maintainer: the basecamp config is committed (artifacts are committed too).

    contributor: it is not (artifacts are regenerable and belong in .gitignore).
    """
    p = _git(repo, "ls-files", "--", ".polymorph/basecamp/config.toml")
    if p.returncode != 0:
        return "unknown"
    return "maintainer" if p.stdout.strip() else "contributor"


def _read_manifest(repo: Path) -> list[Artifact]:
    return manifest_entries(repo)


def write_manifest(repo: Path, artifacts: list[Artifact], links: list[Link]) -> None:
    """Replace the manifest with `artifacts` and `links` (the caller merges with what was there)."""
    manifest = repo / MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest.with_name(manifest.name + ".polymorph-tmp")
    tmp.write_text(_loadout_lines(artifacts, links))
    tmp.replace(manifest)


def record_links(repo: Path, made: list[Link]) -> None:
    """Add `made` to the manifest, keeping every artifact and every other link."""
    keys = {(l.harness, l.path) for l in made}
    kept = [l for l in manifest_links(repo) if (l.harness, l.path) not in keys]
    write_manifest(repo, _read_manifest(repo), [*kept, *made])


def _loadout_lines(artifacts: list[Artifact], links: list[Link] = ()) -> str:
    out = ["# Written by polymorph equip: provenance of the issued loadout.", "schema = 1", ""]
    by_harness: dict[str, list[Artifact]] = {}
    for a in artifacts:
        by_harness.setdefault(a.harness, []).append(a)
    for harness in sorted(by_harness):
        out.append(f"[harness.{harness}]")
        for a in sorted(by_harness[harness], key=lambda x: x.path):
            entry = f'hash = {json.dumps(a.hash)}, source = {json.dumps(a.source)}'
            if a.mapping_path and a.mapping_hash:
                entry += (f', mapping-path = {json.dumps(a.mapping_path)}'
                          f', mapping-hash = {json.dumps(a.mapping_hash)}')
            out.append(f'{json.dumps(a.path)} = {{{entry}}}')
        out.append("")
    by_link: dict[str, list[Link]] = {}
    for l in links:
        by_link.setdefault(l.harness, []).append(l)
    for harness in sorted(by_link):
        out.append(f"[links.{harness}]")
        for l in sorted(by_link[harness], key=lambda x: x.path):
            out.append(f'{json.dumps(l.path)} = {{target = {json.dumps(l.target)}, kind = {json.dumps(l.kind)}}}')
        out.append("")
    return "\n".join(out)


def equip(repo: str | Path = ".", harness: str = "", dry_run: bool = False,
          gitignore: bool = False, make_links: bool = True) -> EquipResult:
    if not harness:
        raise EquipError("equip needs --harness NAME")
    repo = Path(repo).resolve()
    result = EquipResult(harness=harness, dry_run=dry_run)

    gen = generate(repo, [harness], check=dry_run)
    if not gen:
        raise EquipError(f"nothing to equip for {harness!r}: no targets (is {harness} in .polymorph/basecamp/config.toml, or pass a mapping?)")
    r = gen[0]
    if r.undecided:
        result.not_written = [r.path]
        result.undecided = len(r.undecided)
    else:
        if r.drift:
            result.written.append(r.path)
        else:
            result.unchanged.append(r.path)
        if not dry_run and r.path:
            dest = repo / r.path
            if dest.is_file():
                from .mapping import load_mapping

                m = load_mapping(harness, repo)
                msrc = Path(m.source)
                artifact = Artifact(harness=harness, path=r.path, hash=_sha256(dest),
                                    source=f"generated from .agents/settings.json via mapping {harness}",
                                    mapping_path=str(msrc), mapping_hash=_sha256(msrc))
                # every harness's entries stay; this run replaces only its own artifact's
                kept = [a for a in _read_manifest(repo)
                        if (a.harness, a.path) != (artifact.harness, artifact.path)]
                write_manifest(repo, [*kept, artifact], manifest_links(repo))
                result.manifest_written = True

    inv = read_inventory(repo)
    cap: Coverage | None = load_coverage(harness, repo)
    result.coverage_known = cap is not None
    if cap is not None and make_links:
        lp = linkmod.plan(repo, [cap], inv)
        result.link_refused = list(lp.refused)
        if dry_run:
            result.linked = lp.create
        else:
            result.linked, failed = linkmod.apply(repo, lp.create)
            result.link_refused += failed
            if result.linked:
                record_links(repo, result.linked)
                result.manifest_written = True
                inv = read_inventory(repo)
    if cap is not None:
        from .inventory import KINDS, gaps as compute_gaps

        found = {(g.kind, g.path): g for g in compute_gaps(inv, cap, repo)}
        for kind in KINDS:
            for item in inv.kind(kind):
                if (kind, item.path) in found:
                    result.gaps.append(found[(kind, item.path)])
                else:
                    result.honoured.append((kind, item.path))
    # coverage unknown: nothing is claimed; the summary says so

    from .env import ENV_FILE, EnvError, contract_lines, load_env

    if (repo / ENV_FILE).is_file():
        try:
            result.env_lines = contract_lines(load_env(repo), harness)
        except (EnvError, ValueError, OSError) as e:   # EnvError and TOML errors are ValueErrors
            result.env_error = str(e)

    result.posture = _posture(repo)
    if result.posture == "contributor" and (result.written or result.unchanged):
        result.gitignore_suggested = [r.path] if r.path else []
        if gitignore and not dry_run:
            gi = repo / ".gitignore"
            current = gi.read_text() if gi.is_file() else ""
            if GITIGNORE_MARK not in current:
                block = GITIGNORE_MARK + "\n" + "\n".join(sorted(set(result.gitignore_suggested))) + "\n"
                gi.write_text(current + ("\n" if current and not current.endswith("\n") else "") + block)
                result.gitignore_appended = True
    return result


def manifest_links(repo: str | Path = ".", harness: str | None = None) -> list[Link]:
    """The symlinks equip or adopt made, as the manifest records them."""
    import tomllib

    path = Path(repo) / MANIFEST
    if not path.is_file():
        return []
    out: list[Link] = []
    for name, entries in tomllib.loads(path.read_text()).get("links", {}).items():
        if harness is not None and name != harness:
            continue
        for p, v in sorted(entries.items()):
            out.append(Link(harness=name, path=p, target=v["target"], kind=v.get("kind", "")))
    return out


def manifest_entries(repo: str | Path = ".", harness: str | None = None) -> list[Artifact]:
    """Read the provenance manifest back (check-side staleness work reads this too)."""
    import tomllib

    path = Path(repo) / MANIFEST
    if not path.is_file():
        return []
    doc = tomllib.loads(path.read_text())
    out: list[Artifact] = []
    for name, entries in doc.get("harness", {}).items():
        if harness is not None and name != harness:
            continue
        if not isinstance(entries, dict):
            raise EquipError(f"{MANIFEST}: [harness.{name}] must be a table of artifact entries")
        for p, v in sorted(entries.items()):
            if not isinstance(v, dict) or not isinstance(v.get("hash"), str) \
                    or not isinstance(v.get("source"), str):
                raise EquipError(f'{MANIFEST}: [harness.{name}] {json.dumps(p)} must be a table '
                                 "with string `hash` and `source`")
            mp, mh = v.get("mapping-path"), v.get("mapping-hash")
            if mp is None and mh is None:
                out.append(Artifact(harness=name, path=p, hash=v["hash"], source=v["source"]))
            elif isinstance(mp, str) and isinstance(mh, str):
                out.append(Artifact(harness=name, path=p, hash=v["hash"], source=v["source"],
                                    mapping_path=mp, mapping_hash=mh))
            else:
                raise EquipError(f'{MANIFEST}: [harness.{name}] {json.dumps(p)}: mapping-path and '
                                 "mapping-hash must be strings together")
    return out
