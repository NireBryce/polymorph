"""The canonical payload keys and where each lives in the canonical (Claude) payload.

Defined once: the adapter places translated values with it, and validate.py's
direct-delivery identity rule compares a mapping's payload_in against it.
"""
from __future__ import annotations

CANON_PATHS = {
    "tool_name": "tool_name",
    "cwd": "cwd",
    "session_id": "session_id",
    "shell.command": "tool_input.command",
    "edit.file_path": "tool_input.file_path",
    "read.file_path": "tool_input.file_path",
    "read.path": "tool_input.path",
    "read.pattern": "tool_input.pattern",
    "read.glob": "tool_input.glob",
}
