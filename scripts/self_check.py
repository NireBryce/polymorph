#!/usr/bin/env python3
"""Dogfood: run polymorph on its own repository.

    python3 scripts/self_check.py
    just self-check

Fails on an invalid shipped mapping, a failed fixture, or a new gap in this repo's
own agent layout (the basecamp store and its symlinks must stay fully honoured).
Was an inline bash-and-Python recipe in the justfile; a script runs without `just`
(scripts/run_recipe.py) and is read by the tests like any other file.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def polymorph(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run([sys.executable, "-m", "polymorph", *args], cwd=ROOT, env=env,
                          capture_output=capture, text=True)


def main() -> int:
    for mapping_path in sorted((ROOT / "src/polymorph/mappings").glob("*.toml")):
        validate_proc = polymorph("manage", "validate", str(mapping_path.relative_to(ROOT)))
        if validate_proc.returncode != 0:
            return validate_proc.returncode
    conformance_proc = polymorph("manage", "conformance")
    if conformance_proc.returncode != 0:
        return conformance_proc.returncode
    inventory_proc = polymorph("inventory", "--repo", ".", "--json", capture=True)
    if inventory_proc.returncode != 0:
        print(inventory_proc.stderr, file=sys.stderr)
        return inventory_proc.returncode
    gaps = json.loads(inventory_proc.stdout)["gaps"]
    for gap in gaps:
        print(f"gap: {gap['harness']} {gap['kind']} {gap['path']} ({gap['reason']})", file=sys.stderr)
    if gaps:
        print(f"self-check: {len(gaps)} gap(s) in this repo's own agent layout; link or "
              "restructure them, or change the expectation here deliberately", file=sys.stderr)
        return 1
    print("self-check: shipped mappings valid, fixtures pass, inventory reports no gaps")
    return 0


if __name__ == "__main__":
    sys.exit(main())
