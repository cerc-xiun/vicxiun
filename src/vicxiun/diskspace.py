"""Disk-space protection for Capture and Download (ticket #12).

Free space is protected twice, in two different ways:

* **Before a Capture starts**, the target volume must hold at least the
  pre-flight floor. Below it the Capture does not start at all -- a
  nearly-full device is never handed a recording -- and the user is told
  what is required and what is available. A Download is deliberately not
  subject to this floor: it is bounded by the file the platform already
  advertised and is covered by the running check alone.
* **While any Item is being written**, free space is checked
  periodically. Below the running floor the transfer is stopped through
  the *same shutdown path as a deliberate stop*: the hook raises
  ``KeyboardInterrupt`` (the exception a real Ctrl+C raises), so the
  Queue's existing interrupt aftermath runs unchanged -- a Capture keeps
  its playable recording (the MKV container is the whole safety net,
  ADR-0007), a Download is cleaned of partial artifacts, later Items are
  named as not run. A long capture therefore cannot die near the end
  with the disk full and leave nothing behind.

The two floors are plain constants on purpose: they are the whole
tuning surface of this module, and the reasoning for each value lives
next to it.
"""

from __future__ import annotations

import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

# The pre-flight floor for a Capture, in bytes (ticket #12): below this
# much free space the Capture does not start. Two gibibytes is a full
# hour or more of recording at the bitrates live platforms offer, with
# headroom for the merge and finalisation that draw on the same volume;
# a device that cannot promise this much should not be handed a
# recording that can only end in a full disk.
PRE_FLIGHT_FLOOR_BYTES = 2 * 1024 * 1024 * 1024

# The running floor, in bytes (ticket #12): below this much free space
# the transfer in progress is stopped. Half a gibibyte leaves the stop
# itself room to work -- the final fragment write, the container
# finalisation, and for a Download the merge -- while keeping a
# nearly-full device from being pushed over the edge mid-transfer.
RUNNING_FLOOR_BYTES = 500 * 1024 * 1024

# How often the running check re-measures, in seconds (ticket #12).
# Five seconds bounds the overshoot to tens of megabytes at the
# bitrates a stream offers -- never enough to badly overrun the floor --
# and one volume query per interval is noise next to the network I/O.
CHECK_INTERVAL_SECONDS = 5.0


def human_bytes(nbytes: int) -> str:
    """Render a byte count for a user-facing line: ``N.N GB`` or ``N.N MB``."""
    if nbytes >= 1024 * 1024 * 1024:
        return f"{nbytes / (1024**3):.1f} GB"
    return f"{nbytes / (1024**2):.1f} MB"


def free_bytes(path: Path) -> int:
    """Free bytes on the volume that holds (or will hold) ``path``.

    The target's directory may not exist yet -- a first run creates the
    output folders -- so the probe walks up to the nearest existing
    ancestor. Ancestor and target share a volume, and the volume is
    what the floors are about.
    """
    probe = Path(path)
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    return shutil.disk_usage(probe).free


def capture_pre_flight(
    target: Path,
    floor: int = PRE_FLIGHT_FLOOR_BYTES,
) -> str | None:
    """The refusal message for a Capture on a drive below ``floor``.

    ``None`` means the Capture may start. A volume that cannot be
    measured (``OSError``) also lets the run proceed: a guard that
    cannot read the drive must not fail the run, and the running check
    still applies once recording begins.
    """
    try:
        free = free_bytes(target)
    except OSError:
        return None
    if free >= floor:
        return None
    return (
        f"not enough free space to record: {human_bytes(free)} available, "
        f"below the {human_bytes(floor)} a Capture needs before it starts "
        f"({target}). Free up space (or choose another output folder with "
        "--folder) and run again."
    )


class SpaceWatch:
    """A progress hook that stops the transfer when free space is short.

    The hook rides yt-dlp's progress events -- the same events the
    progress printer shows -- and re-measures free space at most once
    per ``interval`` seconds, and on the very first event, so a
    Download started on a nearly-full device is stopped by its first
    progress report. Below the floor it explains the situation (the
    floor, what was available, what to do next) and raises
    ``KeyboardInterrupt``: the same exception a deliberate stop
    raises, so the Queue's shutdown path runs and the aftermath -- the
    kept recording or the cleaned partials -- is decided by
    ``vicxiun.capture``, not here.

    On a *live* fragment download yt-dlp converts that exception into
    a graceful finish (its fragment loop catches it for live streams
    and finalises the recording instead of propagating), so the object
    carries ``tripped``: the Queue checks it after the executor
    returns and finishes the stop on the interrupt path all the same.
    An instance is a progress hook -- the executor is given the
    instance itself.

    ``follow``, when given, is the next hook in the chain and is
    called for every event that does not stop the transfer. An
    unmeasurable volume (``OSError``) skips the check silently --
    printing it would spam one line per interval for the whole
    transfer.
    """

    def __init__(
        self,
        target: Path,
        floor: int = RUNNING_FLOOR_BYTES,
        *,
        follow: Callable[[dict[str, object]], None] | None = None,
        interval: float = CHECK_INTERVAL_SECONDS,
    ) -> None:
        self._target = target
        self._floor = floor
        self._follow = follow
        self._interval = interval
        self._last_checked = float("-inf")
        #: True once the hook stopped the transfer for low space.
        self.tripped = False

    def __call__(self, d: dict[str, object]) -> None:
        now = time.monotonic()
        if now - self._last_checked >= self._interval:
            self._last_checked = now
            free = _measure(self._target)
            if free is not None and free < self._floor:
                self.tripped = True
                print(
                    f"out of disk space: {human_bytes(free)} free, "
                    f"below the {human_bytes(self._floor)} running floor -- "
                    "stopping cleanly. Free up space (or choose another "
                    "output folder with --folder) and run again.",
                    file=sys.stderr,
                    flush=True,
                )
                raise KeyboardInterrupt("free space fell below the running floor")
        if self._follow is not None:
            self._follow(d)


def _measure(target: Path) -> int | None:
    """Free bytes on the target's volume, or None when unmeasurable.

    A volume that cannot be read (an unusual mount, a probe error) is
    skipped: a guard that cannot read the drive must not fail the
    transfer, and printing the skip would spam one line per interval.
    """
    try:
        return free_bytes(target)
    except OSError:
        return None


__all__ = [
    "CHECK_INTERVAL_SECONDS",
    "PRE_FLIGHT_FLOOR_BYTES",
    "RUNNING_FLOOR_BYTES",
    "SpaceWatch",
    "capture_pre_flight",
    "free_bytes",
    "human_bytes",
]
