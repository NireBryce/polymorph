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
    python3 -m compileall -q src tests
    python3 tests/lint.py

# Everything CI runs
preflight: test docs lint check

# Point git at .githooks (pre-commit runs docs and lint)
install-hooks:
    git config core.hooksPath .githooks
