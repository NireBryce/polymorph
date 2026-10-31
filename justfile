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

# Everything CI runs
preflight: test docs lint check

# Point git at .githooks (pre-commit runs docs and lint)
install-hooks:
    git config core.hooksPath .githooks

# Wait for CI on a PR's (or branch's) head commit; exit 0 only when it succeeded and the PR is CLEAN
ci-wait *args:
    python3 scripts/ci_wait.py {{args}}

# Merge a green PR pinned to its head, sync main, delete the branch and its worktree (closes no issues)
ship-land pr *args:
    python3 scripts/ship_land.py {{pr}} {{args}}
