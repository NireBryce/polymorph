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
    written: list[str] = field(default_factory=list)        # dry run: what would be written
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
    proc = _git(repo, "ls-files", "--", ".polymorph/basecamp/config.toml")
    if proc.returncode != 0:
        return "unknown"
    return "maintainer" if proc.stdout.strip() else "contributor"




def write_manifest(repo: Path, artifacts: list[Artifact], links: list[Link]) -> None:
    """Replace the manifest with `artifacts` and `links` (the caller merges with what was there)."""
    manifest = repo / MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest.with_name(manifest.name + ".polymorph-tmp")
    tmp.write_text(_loadout_lines(artifacts, links))
    tmp.replace(manifest)


def record_links(repo: Path, made: list[Link]) -> None:
    """Add `made` to the manifest, keeping every artifact and every other link."""
    keys = {(link.harness, link.path) for link in made}
    kept = [link for link in manifest_links(repo) if (link.harness, link.path) not in keys]
    write_manifest(repo, manifest_entries(repo), [*kept, *made])


def _loadout_lines(artifacts: list[Artifact], links: list[Link] = ()) -> str:
    out = ["# Written by polymorph equip: provenance of the issued loadout.", "schema = 1", ""]
    by_harness: dict[str, list[Artifact]] = {}
    for artifact in artifacts:
        by_harness.setdefault(artifact.harness, []).append(artifact)
    for harness in sorted(by_harness):
        out.append(f"[harness.{harness}]")
        for artifact in sorted(by_harness[harness], key=lambda artifact: artifact.path):
            entry = f'hash = {json.dumps(artifact.hash)}, source = {json.dumps(artifact.source)}'
            if artifact.mapping_path and artifact.mapping_hash:
                entry += (f', mapping-path = {json.dumps(artifact.mapping_path)}'
                          f', mapping-hash = {json.dumps(artifact.mapping_hash)}')
            out.append(f'{json.dumps(artifact.path)} = {{{entry}}}')
        out.append("")
    by_link: dict[str, list[Link]] = {}
    for link in links:
        by_link.setdefault(link.harness, []).append(link)
    for harness in sorted(by_link):
        out.append(f"[links.{harness}]")
        for link in sorted(by_link[harness], key=lambda link: link.path):
            out.append(f'{json.dumps(link.path)} = {{target = {json.dumps(link.target)}, kind = {json.dumps(link.kind)}}}')
        out.append("")
    return "\n".join(out)


def equip(repo: str | Path = ".", harness: str = "", dry_run: bool = False,
          gitignore: bool = False, make_links: bool = True) -> EquipResult:
    if not harness:
        raise EquipError("equip needs --harness NAME")
    repo = Path(repo).resolve()
    result = EquipResult(harness=harness, dry_run=dry_run)

    generated = generate(repo, [harness], check=dry_run)
    if not generated:
        raise EquipError(f"nothing to equip for {harness!r}: no targets (is {harness} in .polymorph/basecamp/config.toml, or pass artifact mapping?)")
    target_result = generated[0]
    if target_result.undecided:
        result.not_written = [target_result.path]
        result.undecided = len(target_result.undecided)
    else:
        if target_result.drift:
            result.written.append(target_result.path)
        else:
            result.unchanged.append(target_result.path)
        if not dry_run and target_result.path:
            dest = repo / target_result.path
            if dest.is_file():
                from .mapping import load_mapping

                mapping = load_mapping(harness, repo)
                mapping_source = Path(mapping.source)
                new_artifact = Artifact(harness=harness, path=target_result.path, hash=_sha256(dest),
                                        source=f"generated from .agents/settings.json via mapping {harness}",
                                        mapping_path=str(mapping_source), mapping_hash=_sha256(mapping_source))
                # every harness's entries stay; this run replaces only its own artifact's
                kept = [artifact for artifact in manifest_entries(repo)
                        if (artifact.harness, artifact.path)
                        != (new_artifact.harness, new_artifact.path)]
                write_manifest(repo, [*kept, new_artifact], manifest_links(repo))
                result.manifest_written = True

    inventory = read_inventory(repo)
    coverage: Coverage | None = load_coverage(harness, repo)
    result.coverage_known = coverage is not None
    if coverage is not None and make_links:
        link_plan = linkmod.plan(repo, [coverage], inventory)
        result.link_refused = list(link_plan.refused)
        if dry_run:
            result.linked = link_plan.create
        else:
            result.linked, failed = linkmod.apply(repo, link_plan.create)
            result.link_refused += failed
            if result.linked:
                record_links(repo, result.linked)
                result.manifest_written = True
                inventory = read_inventory(repo)
    if coverage is not None:
        from .inventory import KINDS, gaps as compute_gaps

        found = {(gap.kind, gap.path): gap for gap in compute_gaps(inventory, coverage, repo)}
        for kind in KINDS:
            for item in inventory.kind(kind):
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
        result.gitignore_suggested = [target_result.path] if target_result.path else []
        if gitignore and not dry_run:
            gitignore_path = repo / ".gitignore"
            current = gitignore_path.read_text() if gitignore_path.is_file() else ""
            if GITIGNORE_MARK not in current:
                block = GITIGNORE_MARK + "\n" + "\n".join(sorted(set(result.gitignore_suggested))) + "\n"
                gitignore_path.write_text(current + ("\n" if current and not current.endswith("\n") else "") + block)
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
        for link_path, entry in sorted(entries.items()):
            out.append(Link(harness=name, path=link_path, target=entry["target"], kind=entry.get("kind", "")))
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
        for artifact_path, entry in sorted(entries.items()):
            if not isinstance(entry, dict) or not isinstance(entry.get("hash"), str) \
                    or not isinstance(entry.get("source"), str):
                raise EquipError(f'{MANIFEST}: [harness.{name}] {json.dumps(artifact_path)} must be a table '
                                 "with string `hash` and `source`")
            mapping_path, mapping_hash = entry.get("mapping-path"), entry.get("mapping-hash")
            if mapping_path is None and mapping_hash is None:
                out.append(Artifact(harness=name, path=artifact_path, hash=entry["hash"], source=entry["source"]))
            elif isinstance(mapping_path, str) and isinstance(mapping_hash, str):
                out.append(Artifact(harness=name, path=artifact_path, hash=entry["hash"], source=entry["source"],
                                    mapping_path=mapping_path, mapping_hash=mapping_hash))
            else:
                raise EquipError(f'{MANIFEST}: [harness.{name}] {json.dumps(artifact_path)}: mapping-path and '
                                 "mapping-hash must be strings together")
    return out
