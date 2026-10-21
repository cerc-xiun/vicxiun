"""Filesystem paths for ``vicxiun``.

Two responsibilities live here, both deliberately pure:

* ``sanitize_filename(title)`` -- turn a free-form title into a name that the
  union of Windows and Android shared-storage rules will accept.
* ``resolve_target(output_root, uploader, title, video_id, ext)`` -- build
  the absolute target path under a ``<output>/<Uploader>/<Title> [id].<ext>``
  layout, with a ``_unknown/`` fallback when the Uploader is missing or
  unusable, and containment asserted independently of the sanitiser.

Path traversal and filename sanitisation are two of the three most dangerous
security surfaces in this project. They are also nearly the only logic in
the codebase with no value in reaching the network for, so they live as pure
functions with exhaustive unit tests (see ``tests/test_paths.py``) and a
parallel integration test through the CLI (see ``tests/test_cli.py``).

The placement of files on disk is described in the project spec under
"Files on disk" and in ``GLOSSARY.md``.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

# Reserved device names on Windows. Match case-insensitively, with or
# without an extension. Android shared storage forbids the same set.
_WINDOWS_RESERVED_BARE: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

# Anything in this set gets stripped entirely. The union of Windows-illegal
# characters plus control characters, which are illegal on both Windows and
# Android shared storage.
_ILLEGAL_CHARS: frozenset[str] = frozenset(
    '<>:"/\\|?*' + "".join(chr(c) for c in range(32))
)

_TRAILING_TRIM_RE = re.compile(r"[\s.]+$")
_COLLAPSE_WS_RE = re.compile(r"\s+")

# The shared-storage layout's fallback group for an Item with no usable
# Uploader. Exposed so that tests and the plan formatter can refer to it
# without re-typing the literal.
UNKNOWN_UPLOADER = "_unknown"

# Defensive ceiling for a single filename segment. Android shared storage
# caps at 255 bytes per segment; Windows at 255 characters; we apply the
# shorter bound with margin so the [id] suffix and extension fit.
_MAX_SEGMENT = 200

_REPLACEMENT = " "


class PathError(Exception):
    """Raised when a path cannot be constructed safely."""


def _build_strip_table() -> Mapping[int, str | None]:
    """A translation table that replaces illegal characters with a space."""
    return {ord(c): _REPLACEMENT for c in _ILLEGAL_CHARS}


_STRIP_TABLE = _build_strip_table()


def sanitize_filename(title: str) -> str:
    """Return a filename-safe version of ``title``.

    The output is always non-empty, never a bare Windows reserved device
    name, never just ``.`` or ``..``, never contains a Windows-illegal
    character, and never ends with a dot or space.

    Determinism matters: ``sanitize_filename(x) == sanitize_filename(x)`` for
    any input. Two same-text titles sanitise the same way.
    """
    cleaned = title.translate(_STRIP_TABLE)
    cleaned = _COLLAPSE_WS_RE.sub(_REPLACEMENT, cleaned)
    cleaned = _TRAILING_TRIM_RE.sub("", cleaned)
    cleaned = cleaned.strip(_REPLACEMENT)

    if cleaned == "" or cleaned in {".", ".."}:
        return "untitled"

    if cleaned.upper() in _WINDOWS_RESERVED_BARE:
        cleaned = f"{cleaned}_"

    if len(cleaned) > _MAX_SEGMENT:
        cleaned = cleaned[:_MAX_SEGMENT].rstrip(_REPLACEMENT)

    return cleaned


def _neutralise_traversal(component: str) -> str:
    """Strip path separators and ``..`` components from a single segment.

    Independent defence-in-depth: even if sanitisation regresses, this keeps
    a hostile title from constructing an absolute or relative path that
    escapes ``output_root``.
    """
    # Strip leading slashes/backslashes/dots that would create absolute or
    # parent references when joined.
    stripped = component.lstrip("/\\.").strip()
    return stripped


def _safe_component(name: str) -> str:
    """Apply ``sanitize_filename`` and ensure non-empty, non-reserved."""
    candidate = sanitize_filename(name)
    if candidate == "" or candidate in {".", ".."}:
        return "untitled"
    candidate = _neutralise_traversal(candidate)
    if candidate == "" or candidate in {".", ".."}:
        return "untitled"
    return candidate


def _is_rooted(segment: str) -> bool:
    """True if the segment, treated as a path component, would escape
    or anchor outside the output folder."""
    if not segment:
        return False
    # Drive letter: "C:" or "C:\\" or "C:/" — Windows-specific escape.
    if len(segment) >= 2 and segment[1] == ":" and segment[0].isalpha():
        return True
    # PurePosixPath treats leading "/" as anchor; we treat both "/foo" and
    # "\\foo" the same way here.
    pure = PurePosixPath(segment.replace("\\", "/"))
    return pure.is_absolute() or any(part == ".." for part in pure.parts)


def _safe_id(video_id: str) -> str:
    """Reduce a video id to the characters a filename segment may hold."""
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", video_id) or "id"
    return safe_id[:_MAX_SEGMENT].rstrip(_REPLACEMENT)


def entry_target(
    folder: str | os.PathLike[str],
    title: str,
    video_id: str,
    ext: str,
) -> Path:
    """Build the absolute target path for one entry of a Playlist.

    Layout (ticket #23): a Playlist's entries land as one file each inside
    the Playlist's own folder, ``<folder>/<Title> [id].<ext>``. The entry
    names go through the same sanitisation and traversal neutralisation
    as ``resolve_target`` -- a hostile entry title can never escape the
    Playlist folder -- and containment is asserted here independently,
    mirroring ``resolve_target``'s defence-in-depth.
    """
    root = Path(os.fspath(folder)).resolve()

    if _is_rooted(title or ""):
        title_segment = "untitled"
    else:
        title_segment = _safe_component(title or "")

    base = f"{title_segment} [{_safe_id(video_id)}]"
    safe_ext = re.sub(r"[^A-Za-z0-9]", "", ext.lower()) or "mp4"
    candidate_path = (root / f"{base}.{safe_ext}").resolve()

    try:
        candidate_path.relative_to(root)
    except ValueError as exc:
        raise PathError(
            f"target {candidate_path} escaped playlist folder {root}"
        ) from exc

    return candidate_path


def resolve_target(
    output_root: str | os.PathLike[str],
    uploader: str,
    title: str,
    video_id: str,
    ext: str,
) -> Path:
    """Build the absolute target path for one Item.

    Layout: ``<output_root>/<Uploader or _unknown>/<Title> [id>].<ext>``

    The output is guaranteed to resolve *inside* ``output_root`` even for
    hostile titles. Containment is enforced *independently* of the
    sanitisation transform -- a test monkeypatches ``sanitize_filename`` to
    the identity and still asserts containment.
    """
    root_str = os.fspath(output_root)
    root_str = os.path.expanduser(root_str)
    root = Path(root_str).resolve()

    # Uploader: empty/whitespace or sanitiser-stripped empty falls back.
    uploader_segment = UNKNOWN_UPLOADER
    trimmed = (uploader or "").strip().strip("/\\")
    if trimmed and trimmed != ".":
        candidate = _safe_component(uploader)
        # If the sanitiser collapses "///" or "   " back to "untitled",
        # treat that as no usable Uploader and fall back.
        if candidate and candidate not in {"", ".", "..", "untitled"}:
            uploader_segment = candidate

    # Title: defence-in-depth against traversal even when sanitiser is
    # bypassed.
    raw_title = title or ""
    if _is_rooted(raw_title):
        title_segment = "untitled"
    else:
        title_segment = _safe_component(raw_title)

    base = f"{title_segment} [{_safe_id(video_id)}]"
    safe_ext = re.sub(r"[^A-Za-z0-9]", "", ext.lower()) or "mp4"
    name = f"{base}.{safe_ext}"

    candidate_path = (root / uploader_segment / name).resolve()

    try:
        candidate_path.relative_to(root)
    except ValueError as exc:
        raise PathError(f"target {candidate_path} escaped output root {root}") from exc

    return candidate_path
