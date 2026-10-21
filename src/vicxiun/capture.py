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

from pathlib import Path

from vicxiun.plan import CaptureKind, Plan

# The instruction is printed before recording begins (user story 13),
# and it says why one interrupt is the whole story: a second interrupt
# risks a damaged file, so there is nothing more for the user to know.
STOP_INSTRUCTION = (
    "stop: press Ctrl+C once to end the Capture -- the recording stays "
    "playable. A second press risks damaging the file."
)


def partial_artifacts(target: Path) -> list[Path]:
    """Return yt-dlp's partial-download artifacts beside ``target``.

    An artifact is any sibling whose name continues ``target``'s with a
    dot: ``.part``, ``.part-FragNN``, ``.ytdl``, and the pre-merge
    ``.fNNN.<ext>`` files. The final file itself is never an artifact:
    its own name does not start with ``<target>.``.
    """
    parent = target.parent
    if not parent.is_dir():
        return []
    prefix = target.name + "."
    return sorted(p for p in parent.iterdir() if p.name.startswith(prefix))


def collect_captured(target: Path) -> Path | None:
    """The file a stopped Capture left behind, if anything survived.

    A clean stop leaves the final file where the Plan said it would
    be. Without a clean shutdown the partial recording (``.part``) is
    the best that survived -- still playable, because the container is
    the whole safety net (ADR-0007). ``None`` means nothing had been
    recorded yet.
    """
    if target.is_file():
        return target
    for artifact in partial_artifacts(target):
        return artifact
    return None


def remove_partial_download(target: Path, preexisting: bool) -> None:
    """Erase the remnants of an interrupted Download.

    Deletes every partial artifact. The final file is deleted only
    when the file did not exist when this Item began: then it is this
    Item's own work -- a merge the interrupt cut short, which no
    player opens. ``preexisting`` is decided by a plain existence
    check taken before the Item ran, not by timestamps: a filesystem
    write timestamp can trail the wall clock by microseconds, which
    makes "written during this Item" undecidable from two adjacent
    writes. A final file that existed before is left alone -- a
    completed Item's file is guaranteed intact (ticket #13) -- the one
    known limit: a interrupted merge of a link the user already has
    can break the previous copy, because ffmpeg truncates it before
    the interrupt could say anything. The Item is simply run again.
    """
    for artifact in partial_artifacts(target):
        artifact.unlink(missing_ok=True)
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
