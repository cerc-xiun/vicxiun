"""Capture and Download safety around an interrupt (ticket #10).

An interrupt at one Item is either a deliberate stop (the Capture the
user is done recording) or an accident. The aftermath differs, and this
module is where it is decided:

* A Capture keeps whatever it recorded. The container -- MKV, with
  each HLS fragment wrapped in MPEG-TS (ADR-0007) -- is the entire
  safety net: a stop, clean or not, leaves a file that opens. The
  interrupt handler therefore deletes nothing for a Capture and hands
  the user the path of what survived.
* A Download keeps nothing partial. An interrupted MP4 is frequently
  unopenable, and a Download is simply run again, so the handler
  removes yt-dlp's partial artifacts (``.part``, ``.part-Frag*``,
  ``.ytdl``, pre-merge ``.fNNN.`` files) and any final file this Item
  created when the interrupt arrived.
"""

from __future__ import annotations

import re
from pathlib import Path

from vicxiun.plan import CaptureKind, Plan

# yt-dlp's partial-download suffixes. Only names ending in one of
# these are removable artifacts: the broad-suffix scan below finds
# candidates cheaply, but deletion is gated on this so a sibling the
# prefix accidentally matches (a thumbnail, a user's own
# ``<target>.old-backup``) is never taken for a partial.
_ARTIFACT_RE = re.compile(
    r"\.(?:part(?:-Frag\d+)?|ytdl|f\d+\.[^\s.]+(?:\.part(?:-Frag\d+)?)?)$"
)

# The instruction is printed before recording begins (user story 13),
# and it says why one interrupt is the whole story: a second interrupt
# risks a damaged file, so there is nothing more for the user to know.
STOP_INSTRUCTION = (
    "stop: press Ctrl+C once to end the Capture -- the recording stays "
    "playable. A second press risks damaging the file."
)


def partial_artifacts(target: Path) -> list[Path]:
    """Return yt-dlp's partial-download artifacts beside ``target``.

    An artifact is a sibling whose name continues ``target``'s with a
    dot and then one of yt-dlp's partial markers: ``.part``,
    ``.part-FragNN``, ``.ytdl``, or a pre-merge ``.fNNN.<ext>`` file.
    Anything else that merely starts with the target's name (a
    thumbnail, the user's own backup) is not ours to touch.
    """
    parent = target.parent
    if not parent.is_dir():
        return []
    prefix = target.name + "."
    return sorted(
        p
        for p in parent.iterdir()
        if p.name.startswith(prefix) and _ARTIFACT_RE.search(p.name)
    )


# For reporting a stopped Capture: rank what survived. A ``.part``
# file is the recording proper; a raw ``.fNNN.<ext>`` fragment is one
# video-only stream no player will frame alone, so it is reported
# last (it is deleted either way).
_KEEP_RANK = (".part", ".part-Frag", ".ytdl")


def _rank_artifact(path: Path) -> tuple[int, str]:
    name = path.name
    for rank, marker in enumerate(_KEEP_RANK):
        if marker in name:
            return rank, name
    return len(_KEEP_RANK), name


def collect_captured(target: Path) -> Path | None:
    """The file a stopped Capture left behind, if anything survived.

    A clean stop leaves the final file where the Plan said it would
    be. Without a clean shutdown whatever ``.part`` recording survived
    is reported -- still playable, because the container is the whole
    safety net (ADR-0007). ``None`` means nothing had been recorded
    yet.
    """
    if target.is_file():
        return target
    artifacts = partial_artifacts(target)
    if not artifacts:
        return None
    return min(artifacts, key=_rank_artifact)


def remove_partial_download(target: Path, preexisting: bool) -> None:
    """Erase the remnants of an interrupted Download.

    For a single video, ``target`` is the file itself: every partial
    artifact beside it is deleted, and the final file is deleted only
    when it did not exist when this Item began -- then it is this
    Item's own work, a merge the interrupt cut short, which no player
    opens. ``preexisting`` is decided by a plain existence check taken
    before the Item ran, not by timestamps: a filesystem write
    timestamp can trail the wall clock by microseconds, which makes
    "written during this Item" undecidable from two adjacent writes. A
    final file that existed before is left alone -- a completed Item's
    file is guaranteed intact (ticket #13) -- and the one known limit
    is the copy a re-run of the SAME link already had completed before
    this Job began, where an interrupted merge lands after ffmpeg has
    truncated the old copy: the wreckage is left in place, and
    re-running finishes the job.

    For a Playlist (ticket #23) ``target`` is the Playlist's folder:
    the partial artifacts of the entry in flight live inside it. They
    are deleted the same way; the finished entries -- complete files --
    stay, which is the completed-Items guarantee applied per entry.
    Deletion is per-file best-effort: on Windows yt-dlp can still hold
    an open handle when the interrupt arrives, and one busy file must
    not stop the rest.
    """
    if target.is_dir():
        # Inside a playlist folder every file vicxiun or yt-dlp writes
        # is an entry's own, and a finished entry always ends in the
        # container extension, so a partial-suffix match cannot hit a
        # final file. A visitor's own file that happens to end in one
        # is out of scope here -- the folder is vicxiun's.
        for artifact in target.iterdir():
            if not _ARTIFACT_RE.search(artifact.name):
                continue
            try:
                artifact.unlink(missing_ok=True)
            except OSError:
                # Still held by a dying yt-dlp (Windows). Next run
                # cleans it: nothing here is worth recovering.
                pass
        return
    for artifact in partial_artifacts(target):
        try:
            artifact.unlink(missing_ok=True)
        except OSError:
            # Still held by a dying yt-dlp (Windows). Next run cleans
            # it: nothing here is worth recovering.
            pass
    if target.is_file() and not preexisting:
        target.unlink()


def after_interrupt(plan: Plan, preexisting: bool) -> tuple[str, Path | None]:
    """The message and surviving file for an Item interrupted mid-run.

    ``preexisting`` says whether the Item's target file already
    existed when the Item began (the Queue snapshots it just before
    the executor runs). Returns ``(detail, path)`` where ``detail``
    reads as the outcome and ``path`` is the file to report (a
    Download reports none).
    """
    if isinstance(plan.kind, CaptureKind):
        kept = collect_captured(plan.target_path)
        if kept is None:
            return "Capture stopped before anything was recorded", None
        return f"Capture stopped; recording kept at {kept}", kept
    remove_partial_download(plan.target_path, preexisting)
    return (
        "interrupted mid-Download; partial files removed, nothing unopenable remains",
        None,
    )


__all__ = [
    "STOP_INSTRUCTION",
    "after_interrupt",
    "collect_captured",
    "partial_artifacts",
    "remove_partial_download",
]
