"""Check that the machine is ready to run ``vicxiun``.

The Prerequisite check is the only place that asks "is this machine ready?".
It is intentionally read-only: it detects missing or out-of-date Prerequisite
programs, prints the exact command the user should run to fix it, and stops.
It never installs anything, never edits ``PATH``, never reads or writes the
Windows registry, and never asks for administrator rights.

Four seams:

* ``check()`` -- pure classifier that turns a ``ProbeResult`` into a
  ``PrereqReport``.
* ``format_message()`` -- pure formatter that turns a ``PrereqReport`` into
  the user-facing text, varying by platform.
* ``probe()`` -- runs ``shutil.which`` and version subprocesses.
* ``run()`` -- the top-level orchestrator. Composes the others and returns
  the process exit code.

The pure functions are testable without I/O; the I/O lives at the edges.
"""

from __future__ import annotations

import datetime
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

# The three Prerequisite programs the tool relies on. ffmpeg muxes audio and
# video into one file; node runs the JavaScript challenge solver bundled with
# yt-dlp; yt-dlp itself fetches the media. yt-dlp is treated specially:
# missing yt-dlp is the same kind of failure as missing ffmpeg or node, but an
# out-of-date yt-dlp is an advisory rather than a hard failure, because yt-dlp
# ships its own ``-U`` self-update and the user can opt out.
_REQUIRED_PROGRAMS: tuple[str, ...] = ("ffmpeg", "node")
_SELF_UPDATING_PROGRAMS: tuple[str, ...] = ("yt-dlp",)
# Anything older than this many calendar years is treated as "stale" for
# self-updating programs. The number is intentionally tight: yt-dlp ships
# several releases a month and its own docs treat anything more than a
# few weeks old as a problem. A few-month window catches the user who
# forgot to update this year without nagging every dev who has a
# slightly old build. Versions strictly older than this threshold are
# flagged. The threshold is a pure function of the local clock; no
# network call is made (ADR-0004).
_STALE_AFTER_YEARS = 0.25


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlatformInfo:
    """The platform the tool is running on.

    ``system`` is the value ``platform.system()`` returns
    (``"Linux"``, ``"Darwin"``, ``"Windows"`` and so on). ``is_termux`` is
    True when the process is running inside Termux on Android.
    """

    system: str
    is_termux: bool = False


@dataclass(frozen=True)
class MissingProgram:
    """A Prerequisite program the tool needs but could not find on ``PATH``."""

    name: str
    what_for: str
    install_command: str


@dataclass(frozen=True)
class StaleProgram:
    """A self-updating Prerequisite program whose version is too old."""

    name: str
    found_version: str
    update_command: str


@dataclass(frozen=True)
class PrereqReport:
    """The result of asking "is this machine ready?".

    ``missing`` is a hard failure: the tool cannot run without these. ``stale``
    is an advisory: the tool can run, but the user probably wants to update.
    """

    missing: list[MissingProgram] = field(default_factory=list)
    stale: list[StaleProgram] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.stale


@dataclass(frozen=True)
class ProbeResult:
    """The raw information gathered by ``probe()``.

    ``which`` is a mapping from program name to its ``shutil.which`` result
    (``None`` when the program is not on ``PATH``). ``versions`` maps the
    program name to its reported version string (empty when the program is
    missing or the version could not be determined).
    """

    which: Mapping[str, str | None]
    versions: Mapping[str, str]
    platform: PlatformInfo


# ---------------------------------------------------------------------------
# Install-command table
# ---------------------------------------------------------------------------
#
# The exact wording is part of the user-facing message and is tested. Each
# entry says, in plain English, what the program is needed for, and gives the
# command to install it on the relevant platform. The four combinations are
# Termux (Android), Linux (Debian/Ubuntu-family), macOS (Homebrew) and Windows
# (winget). Homebrew is the macOS default in the in-tool message because it
# is the most common path; MacPorts and other alternatives are documented in
# ``docs/install-macos.md``.

_INSTALL_GUIDANCE: dict[tuple[str, bool], dict[str, tuple[str, str]]] = {
    # (system, is_termux) -> {program_name: (what_for, install_command)}
    ("Linux", True): {
        "ffmpeg": (
            "muxing audio and video into one playable file",
            "pkg install ffmpeg",
        ),
        "node": (
            "running the JavaScript code that solves site challenges",
            "pkg install nodejs",
        ),
    },
    ("Linux", False): {
        "ffmpeg": (
            "muxing audio and video into one playable file",
            "sudo apt install ffmpeg",
        ),
        "node": (
            "running the JavaScript code that solves site challenges",
            "sudo apt install nodejs",
        ),
    },
    ("Darwin", False): {
        "ffmpeg": (
            "muxing audio and video into one playable file",
            "brew install ffmpeg",
        ),
        "node": (
            "running the JavaScript code that solves site challenges",
            "brew install node",
        ),
    },
    ("Windows", False): {
        "ffmpeg": (
            "muxing audio and video into one playable file",
            "winget install Gyan.FFmpeg",
        ),
        "node": (
            "running the JavaScript code that solves site challenges",
            "winget install OpenJS.NodeJS.LTS",
        ),
    },
}


def _fallback_guidance(name: str, system: str) -> tuple[str, str]:
    """Best-effort guidance for an unsupported platform.

    The wording is intentionally generic and still names the program and what
    it is for; the install command is left as a placeholder the user has to
    fill in, because we genuinely do not know the right one for their
    distribution. This branch is for the long tail; the tests cover the four
    documented platforms.
    """
    what_for_fallback: dict[str, str] = {
        "ffmpeg": "muxing audio and video into one playable file",
        "node": "running the JavaScript code that solves site challenges",
    }
    what_for = what_for_fallback.get(name, "running part of vicxiun")
    return what_for, (
        f"install {name} using whatever your {system} package manager provides"
    )


# ---------------------------------------------------------------------------
# Seam 1: check()
# ---------------------------------------------------------------------------


def check(probe_result: ProbeResult) -> PrereqReport:
    """Classify a ``ProbeResult`` into a ``PrereqReport``.

    Pure: no I/O, no subprocess, no filesystem. The tests pin behaviour at
    this seam.
    """
    missing: list[MissingProgram] = []
    for name in _REQUIRED_PROGRAMS:
        if probe_result.which.get(name) is None:
            what_for, install_command = _guidance_for(name, probe_result.platform)
            missing.append(
                MissingProgram(
                    name=name,
                    what_for=what_for,
                    install_command=install_command,
                )
            )

    stale: list[StaleProgram] = []
    for name in _SELF_UPDATING_PROGRAMS:
        # yt-dlp is a declared Python dependency, so an absent yt-dlp on
        # ``PATH`` is not a Prerequisite failure -- the Python package
        # itself cannot have been installed in that case. The check below
        # therefore only fires when yt-dlp was actually probed for.
        if name not in probe_result.which:
            continue
        if probe_result.which.get(name) is None:
            continue
        found_version = probe_result.versions.get(name, "")
        if _is_stale(found_version):
            stale.append(
                StaleProgram(
                    name=name,
                    found_version=found_version,
                    update_command=f"{name} -U",
                )
            )

    return PrereqReport(missing=missing, stale=stale)


def _guidance_for(name: str, platform: PlatformInfo) -> tuple[str, str]:
    """Return ``(what_for, install_command)`` for a program on a platform.

    The ``what_for`` text starts with the program name so that a glance at the
    description tells the user which program is being talked about.
    """
    table = _INSTALL_GUIDANCE
    entry = table.get((platform.system, platform.is_termux), {}).get(name)
    if entry is not None:
        what_for, install_command = entry
        # Prefix the program name so the description is self-contained.
        return f"{name} ({what_for})", install_command
    return _fallback_guidance(name, platform.system)


def _is_stale(found_version: str) -> bool:
    """A conservative heuristic: a yt-dlp version strictly older than
    ``_STALE_AFTER_YEARS`` of a year before "now" is stale.

    The format yt-dlp prints is ``YYYY.MM.DD``. We compare on a month axis
    so the threshold can be set in fractions of a year (e.g. 3 months ==
    0.25 years). We do not chase the exact "is this older than the latest
    release" question because (a) we do not fetch code at runtime
    (ADR-0004) and (b) the advisory is exactly that -- advice. A false
    positive is harmless; a false negative just delays the update.
    """
    if not found_version:
        return False
    head = found_version.split("+", 1)[0].split("-", 1)[0]
    parts = head.split(".")
    if len(parts) < 3:
        return False
    if not (parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit()):
        return False
    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError:
        return False
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    months_old = (now.year - year) * 12 + (now.month - month)
    threshold_months = _STALE_AFTER_YEARS * 12
    return months_old > threshold_months


# ---------------------------------------------------------------------------
# Seam 2: format_message()
# ---------------------------------------------------------------------------


def format_message(report: PrereqReport, platform: PlatformInfo) -> str:
    """Render a ``PrereqReport`` as the user-facing message text.

    Pure: takes the report and platform as inputs, returns a string. The
    tests pin the Windows / Linux / Termux wording at this seam.
    """
    if not report.missing and not report.stale:
        return ""

    blocks: list[str] = []
    if report.missing:
        blocks.append(_format_missing_block(report.missing))
    if report.stale:
        blocks.append(_format_stale_block(report.stale))
    return "\n\n".join(blocks) + "\n"


def _format_missing_block(items: list[MissingProgram]) -> str:
    if len(items) == 1:
        item = items[0]
        return (
            f"vicxiun cannot find {item.name}.\n"
            f"It is needed for {item.what_for}.\n"
            f"To install it, run:\n\n"
            f"  {item.install_command}\n"
        )
    lines = ["vicxiun is missing the following programs:"]
    for item in items:
        lines.append(f"  - {item.name}: {item.what_for}")
        lines.append(f"      install with: {item.install_command}")
    return "\n".join(lines)


def _format_stale_block(items: list[StaleProgram]) -> str:
    if len(items) == 1:
        item = items[0]
        return (
            f"Note: {item.name} {item.found_version} is out of date. "
            f"vicxiun will still work, but to update run:\n\n"
            f"  {item.update_command}\n"
        )
    lines = ["Note: the following programs are out of date:"]
    for item in items:
        lines.append(f"  - {item.name} {item.found_version}: {item.update_command}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Seam 3: probe()
# ---------------------------------------------------------------------------


def probe() -> ProbeResult:
    """Look up the Prerequisite programs on this machine.

    Runs ``shutil.which`` for each known program, and probes yt-dlp for its
    version. Never spawns an installer.
    """
    programs = _REQUIRED_PROGRAMS + _SELF_UPDATING_PROGRAMS
    which_results: dict[str, str | None] = {}
    for name in programs:
        which_results[name] = shutil.which(name)

    versions: dict[str, str] = {}
    if which_results.get("yt-dlp") is not None:
        versions["yt-dlp"] = _yt_dlp_version()

    return ProbeResult(
        which=which_results,
        versions=versions,
        platform=detect_platform(),
    )


def detect_platform() -> PlatformInfo:
    """Return a ``PlatformInfo`` describing the current platform.

    Termux is detected by either the ``PREFIX`` ending in ``/com.termux`` or
    the presence of ``TERMUX_VERSION`` in the environment. Either signal
    alone is enough.
    """
    env = os.environ
    prefix = env.get("PREFIX", "")
    is_termux = prefix.endswith(("/com.termux", "\\com.termux"))
    if not is_termux and env.get("TERMUX_VERSION"):
        # Termux also exports ANDROID_ROOT and TERMUX_VERSION.
        is_termux = True
    return PlatformInfo(system=platform.system(), is_termux=is_termux)


def _yt_dlp_version() -> str:
    """Return yt-dlp's reported version, or ``""`` on failure.

    Runs ``yt-dlp --version`` with stdout captured. The subprocess is
    read-only: it does not modify the system.
    """
    try:
        completed = subprocess.run(
            ["yt-dlp", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


# ---------------------------------------------------------------------------
# Seam 4: run()
# ---------------------------------------------------------------------------


def run(
    *,
    env: Mapping[str, str] | None = None,
    probe: Callable[[], ProbeResult] = probe,
) -> int:
    """The top-level orchestrator.

    Runs the probe, classifies the result, prints the user-facing text to
    stderr, and returns the process exit code. ``env`` is accepted for
    tests; in production the orchestrator reads the live environment via
    ``detect_platform``.

    Returns ``0`` when nothing is missing (stale programs are an advisory,
    not a failure). Returns non-zero when one or more Prerequisite
    programs are missing.

    Never spawns an installer. Never writes ``PATH``. Never reads or writes
    the Windows registry. Never asks for administrator rights.
    """
    probe_result = probe()
    report = check(probe_result)
    message = format_message(report, probe_result.platform)
    if message:
        print(message, file=sys.stderr, end="")
    return 1 if report.missing else 0
