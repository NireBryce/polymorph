"""Where the shipped data (built-in mappings and conformance fixtures) lives.

Both directories are package data under polymorph/, so they are found the same
way from a source tree, an editable install and a wheel. Every lookup of them
goes through here.
"""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

# The package is installed unzipped, so the resource traversable is a real directory.
DATA_ROOT = Path(str(files("polymorph")))
MAPPINGS_DIR = DATA_ROOT / "mappings"
FIXTURES_DIR = DATA_ROOT / "fixtures"
DATA_DIR = DATA_ROOT / "data"   # files `capture` installs into a scratch directory
