"""Persisted defaults, overridden per-run by flags (ticket #16).

A choice is made once and remembered: the settings file in the
platform's configuration location (``vicxiun.platform.settings_path``)
holds the saved values, and every normal run uses them when no flag is
given. A flag on a given run wins for that run only and is never
written back.

The file is JSON, indented, and safe to edit by hand. A corrupt or
unreadable file never crashes a run: it is named in a warning and the
built-in defaults are used instead.
"""

from __future__ import annotations

import json
from pathlib import Path

# The keys vicxiun persists today. Anything else in the file is
# ignored, so a hand-edited file cannot poison a run.
KNOWN_KEYS: tuple[str, ...] = ("profile", "folder")


def load(path: Path) -> tuple[dict[str, str], str | None]:
    """Return ``(saved values, warning)``.

    A missing file yields ``({}, None)``. A corrupt or unreadable file
    yields ``({}, warning)`` so the caller can carry on with the
    built-in defaults instead of crashing.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return {}, f"Ignoring unreadable settings file {path}: {exc}"
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return {}, f"Ignoring corrupt settings file {path}: {exc}"
    if not isinstance(data, dict):
        return {}, (
            f"Ignoring corrupt settings file {path}: "
            "expected a JSON object at the top level."
        )
    values = {
        key: value
        for key, value in data.items()
        if key in KNOWN_KEYS and isinstance(value, str) and value
    }
    return values, None


def save(path: Path, values: dict[str, str]) -> None:
    """Write ``values`` as human-readable, hand-editable JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(values, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
