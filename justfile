# List the recipes
default:
    @just --list

# The unit tests
test:
    PYTHONPATH=src python3 -m unittest discover -s tests

# nix flake check: builds the package and runs the tests in the sandbox (git add new files first)
check:
    nix flake check

# Mechanical doc checks: skills listed, links resolve, every CLI subcommand documented, banner, no home paths
docs:
    python3 tests/docs.py

# Byte-compile, stdlib-only imports, no committed build output
lint:
    python3 -m compileall -q src tests scripts
    python3 tests/lint.py

# Rewrite docs/usage.md and man/polymorph.1 from the real argparse parser
reference:
    python3 scripts/gen_reference.py

# Fail if docs/usage.md or man/polymorph.1 differ from the parser
reference-check:
    python3 scripts/gen_reference.py --check

# Dogfood: the tool reads its own repo. Fails on an invalid shipped mapping, a
# failed fixture, or a new gap in this repo's own agent layout (the basecamp
# store and its symlinks must stay fully honoured).
self-check:
    #!/usr/bin/env bash
    set -euo pipefail
    for m in src/polymorph/mappings/*.toml; do
        PYTHONPATH=src python3 -m polymorph manage validate "$m"
    done
    PYTHONPATH=src python3 -m polymorph manage conformance
    # heredoc body lines carry the recipe indentation: just strips it before bash
    # runs this, and an under-indented line is parsed as justfile syntax instead
    PYTHONPATH=src python3 - <<'EOF'
    import json, os, subprocess, sys

    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    inventory_proc = subprocess.run([sys.executable, "-m", "polymorph", "inventory", "--repo", ".", "--json"],
                                    capture_output=True, text=True, env=env)
    if inventory_proc.returncode != 0:
        print(inventory_proc.stderr, file=sys.stderr)
        sys.exit(inventory_proc.returncode)
    gaps = json.loads(inventory_proc.stdout)["gaps"]
    for gap in gaps:
        print(f"gap: {gap['harness']} {gap['kind']} {gap['path']} ({gap['reason']})", file=sys.stderr)
    if gaps:
        print(f"self-check: {len(gaps)} gap(s) in this repo's own agent layout; link or "
              "restructure them, or change the expectation here deliberately", file=sys.stderr)
        sys.exit(1)
    print("self-check: shipped mappings valid, fixtures pass, inventory reports no gaps")
    EOF

# Everything CI runs
preflight: test docs lint check self-check

# Point git at .githooks (pre-commit runs docs and lint)
install-hooks:
    git config core.hooksPath .githooks

# Wait for CI on a PR's (or branch's) head commit; exit 0 only when it succeeded and the PR is CLEAN
ci-wait *args:
    python3 scripts/ci_wait.py {{args}}

# Merge a green PR pinned to its head, fast-forward its base branch, delete the branch and its worktree (closes no issues)
ship-land pr *args:
    python3 scripts/ship_land.py {{pr}} {{args}}
