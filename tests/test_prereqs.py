"""Tests for the Prerequisite health check.

The Prereq module answers one question — "is this machine ready?" — in one
place. The tests pin behaviour at the four seams of that module:

1. ``check()`` — pure classifier that turns a ``ProbeResult`` into a
   ``PrereqReport`` (what is missing, what is out of date).
2. ``format_message()`` — pure formatter that turns a ``PrereqReport`` into
   user-facing text, varying by platform.
3. ``probe()`` — runs ``shutil.which`` / version subprocesses and returns a
   ``ProbeResult``.
4. ``run()`` — the top-level orchestrator: composes the others, returns an
   exit code, and guarantees nothing is installed, no PATH write, no
   registry access and no admin rights are needed.
"""

from __future__ import annotations

import datetime
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from vicxiun.prereqs import (
    MissingProgram,
    PlatformInfo,
    PrereqReport,
    ProbeResult,
    StaleProgram,
    check,
    format_message,
    probe,
    run,
)

# ---------------------------------------------------------------------------
# Seam 1: check() -- the pure classifier
# ---------------------------------------------------------------------------


def test_check_reports_missing_ffmpeg_when_not_on_path() -> None:
    probe_result = ProbeResult(
        which={"ffmpeg": None, "node": "/usr/bin/node"},
        versions={"node": "v20.0.0"},
        platform=PlatformInfo(system="Linux", is_termux=False),
    )

    report = check(probe_result)

    missing = report.missing
    assert len(missing) == 1
    assert missing[0].name == "ffmpeg"
    assert "ffmpeg" in missing[0].what_for
    assert missing[0].install_command  # exact wording is platform-specific


def test_check_reports_missing_node_when_not_on_path() -> None:
    probe_result = ProbeResult(
        which={"ffmpeg": "/usr/bin/ffmpeg", "node": None},
        versions={},
        platform=PlatformInfo(system="Linux", is_termux=False),
    )

    report = check(probe_result)

    missing = report.missing
    assert len(missing) == 1
    assert missing[0].name == "node"


def test_check_reports_nothing_missing_when_all_present() -> None:
    probe_result = ProbeResult(
        which={"ffmpeg": "/usr/bin/ffmpeg", "node": "/usr/bin/node"},
        versions={"ffmpeg": "6.0", "node": "v20.0.0"},
        platform=PlatformInfo(system="Linux", is_termux=False),
    )

    report = check(probe_result)

    assert report.missing == []
    assert report.stale == []


def test_check_reports_outdated_ytdlp_as_stale_not_missing() -> None:
    probe_result = ProbeResult(
        which={
            "ffmpeg": "/usr/bin/ffmpeg",
            "node": "/usr/bin/node",
            "yt-dlp": "/usr/bin/yt-dlp",
        },
        # A version that is unambiguously old: from a decade ago. The
        # staleness rule is tight (a few months), but we use an even older
        # fixture so the test is robust to calendar drift.
        versions={"yt-dlp": "2014.01.01"},
        platform=PlatformInfo(system="Linux", is_termux=False),
    )

    report = check(probe_result)

    assert report.missing == []
    assert len(report.stale) == 1
    assert report.stale[0].name == "yt-dlp"


def test_check_does_not_flag_ytdlp_as_stale_within_threshold() -> None:
    """A yt-dlp version released within the last few months is fresh."""
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    # The current calendar month stays well within the tight staleness
    # rule (a few months).
    versions = {"yt-dlp": f"{now.year}.{now.month:02d}.01"}
    probe_result = ProbeResult(
        which={
            "ffmpeg": "/usr/bin/ffmpeg",
            "node": "/usr/bin/node",
            "yt-dlp": "/usr/bin/yt-dlp",
        },
        versions=versions,
        platform=PlatformInfo(system="Linux", is_termux=False),
    )

    report = check(probe_result)

    assert report.stale == []


# ---------------------------------------------------------------------------
# Seam 2: format_message() -- the pure formatter
# ---------------------------------------------------------------------------


def test_format_message_for_missing_ffmpeg_on_linux() -> None:
    report = PrereqReport(
        missing=[
            MissingProgram(
                name="ffmpeg",
                what_for="muxing audio and video into one file",
                install_command=("sudo apt install ffmpeg"),
            )
        ],
        stale=[],
    )
    platform = platform_info(system="Linux", is_termux=False)

    message = format_message(report, platform)

    assert "ffmpeg" in message
    assert "muxing" in message
    assert "sudo apt install ffmpeg" in message


def test_format_message_for_missing_ffmpeg_on_termux() -> None:
    report = PrereqReport(
        missing=[
            MissingProgram(
                name="ffmpeg",
                what_for="muxing audio and video",
                install_command="pkg install ffmpeg",
            )
        ],
        stale=[],
    )
    platform = platform_info(system="Linux", is_termux=True)

    message = format_message(report, platform)

    assert "pkg install ffmpeg" in message
    assert "sudo" not in message


def test_format_message_for_missing_ffmpeg_on_windows() -> None:
    report = PrereqReport(
        missing=[
            MissingProgram(
                name="ffmpeg",
                what_for="muxing audio and video",
                install_command=("winget install Gyan.FFmpeg"),
            )
        ],
        stale=[],
    )
    platform = platform_info(system="Windows", is_termux=False)

    message = format_message(report, platform)

    assert "winget install Gyan.FFmpeg" in message


def test_format_message_for_missing_ffmpeg_on_macos() -> None:
    """The Darwin / macOS branch is exercised by Homebrew commands."""
    report = PrereqReport(
        missing=[
            MissingProgram(
                name="ffmpeg",
                what_for="muxing audio and video",
                install_command="brew install ffmpeg",
            )
        ],
        stale=[],
    )
    platform = platform_info(system="Darwin", is_termux=False)

    message = format_message(report, platform)

    assert "brew install ffmpeg" in message
    assert "sudo" not in message


def test_format_message_for_missing_node_on_macos() -> None:
    report = PrereqReport(
        missing=[
            MissingProgram(
                name="node",
                what_for="running the JavaScript code",
                install_command="brew install node",
            )
        ],
        stale=[],
    )
    platform = platform_info(system="Darwin", is_termux=False)

    message = format_message(report, platform)

    assert "brew install node" in message


def test_format_message_for_stale_ytdlp_is_a_warning_not_an_error() -> None:
    report = PrereqReport(
        missing=[],
        stale=[
            StaleProgram(
                name="yt-dlp", found_version="2024.01.01", update_command="yt-dlp -U"
            )
        ],
    )
    platform = platform_info(system="Linux", is_termux=False)

    message = format_message(report, platform)

    assert "yt-dlp" in message
    assert "yt-dlp -U" in message
    # An advisory warning, not a "what is missing" message.
    assert "missing" not in message.lower()


def test_format_message_is_silent_when_everything_is_present() -> None:
    report = PrereqReport(missing=[], stale=[])
    platform = platform_info(system="Linux", is_termux=False)

    assert format_message(report, platform) == ""


# ---------------------------------------------------------------------------
# Seam 3: probe() -- the I/O wrapper around shutil.which / subprocess
# ---------------------------------------------------------------------------


def test_probe_finds_ffmpeg_on_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None
    )

    result = probe()

    assert result.which["ffmpeg"] == "/usr/bin/ffmpeg"


def test_probe_returns_none_for_missing_program(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)

    result = probe()

    assert result.which["ffmpeg"] is None
    assert result.which["node"] is None


def test_probe_never_runs_an_installer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """probe() only *looks*; it never invokes an install command."""

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        argv = args[0]
        assert isinstance(argv, list)
        # The only subprocess we ever start is the version probes.
        if isinstance(argv[0], str) and argv[0].endswith("yt-dlp"):
            return subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout="2024.01.01",
                stderr="",
            )
        raise AssertionError(f"probe() spawned an unexpected subprocess: {argv}")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(subprocess, "run", fake_run)

    probe()


# ---------------------------------------------------------------------------
# Seam 4: run() -- the orchestrator that returns the exit code
# ---------------------------------------------------------------------------


def test_run_returns_nonzero_when_ffmpeg_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("PATH", "")

    code = run(
        probe=lambda: ProbeResult(
            which={"ffmpeg": None, "node": None, "yt-dlp": None},
            versions={},
            platform=PlatformInfo(system="Linux", is_termux=False),
        )
    )

    captured = capsys.readouterr()
    assert code != 0
    assert "ffmpeg" in captured.err


def test_run_returns_zero_when_everything_is_present(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_probe() -> ProbeResult:
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        # Current calendar month keeps the fixture robust against the
        # tight staleness rule (a few months).
        versions = {"yt-dlp": f"{now.year}.{now.month:02d}.01"}
        return ProbeResult(
            which={
                "ffmpeg": "/usr/bin/ffmpeg",
                "node": "/usr/bin/node",
                "yt-dlp": "/usr/bin/yt-dlp",
            },
            versions=versions,
            platform=PlatformInfo(system="Linux", is_termux=False),
        )

    code = run(probe=fake_probe)

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == ""
    assert captured.err == ""


def test_run_returns_zero_with_advisory_when_ytdlp_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_probe() -> ProbeResult:
        return ProbeResult(
            which={
                "ffmpeg": "/usr/bin/ffmpeg",
                "node": "/usr/bin/node",
                "yt-dlp": "/usr/bin/yt-dlp",
            },
            # A version that is unambiguously old: many years old.
            versions={"yt-dlp": "2014.01.01"},
            platform=PlatformInfo(system="Linux", is_termux=False),
        )

    code = run(probe=fake_probe)

    captured = capsys.readouterr()
    assert code == 0
    assert "yt-dlp" in captured.err


def test_run_never_spawns_an_installer_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run() orchestrator must not start an installer subprocess."""
    spawned: list[list[str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        argv = args[0]
        assert isinstance(argv, list)
        spawned.append([str(part) for part in argv])
        if str(argv[0]).endswith("yt-dlp"):
            return subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout="2024.06.01",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "run", fake_run)

    run()

    installer_signals = (
        "install",
        "add-apt",
        "add-repo",
        "apt-get",
        "pkg",
        "winget",
        "choco",
        "scoop",
        "brew install",
        "pip install",
        "uv pip install",
    )
    for argv in spawned:
        joined = " ".join(argv).lower()
        assert not any(signal in joined for signal in installer_signals), (
            f"run() spawned a process that looks like an installer: {argv}"
        )


# ---------------------------------------------------------------------------
# Static guarantees: no PATH write, no registry, no admin
# ---------------------------------------------------------------------------


def test_prereqs_module_never_writes_to_PATH() -> None:
    """vicxiun.prereqs must never call ``os.environ.__setitem__``."""
    import vicxiun.prereqs as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "environ[" not in source
    assert "__setitem__" not in source


def test_prereqs_module_never_imports_winreg() -> None:
    """vicxiun.prereqs must never read or write the Windows registry."""
    import vicxiun.prereqs as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "winreg" not in source


def test_prereqs_module_never_uses_ctypes_for_admin_checks() -> None:
    """vicxiun.prereqs must never call admin/elevation APIs."""
    import vicxiun.prereqs as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "ctypes" not in source
    assert "IsUserAnAdmin" not in source


def test_run_does_not_mutate_the_live_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavioural guarantee: ``run()`` must not mutate ``os.environ``.

    The static-grep tests above catch literal references to the dangerous
    APIs; this one proves the live environment is unchanged after a real
    invocation, regardless of how the write would have been done.
    """
    snapshot = dict(os.environ)

    def fake_probe() -> ProbeResult:
        return ProbeResult(
            which={"ffmpeg": None, "node": None, "yt-dlp": None},
            versions={},
            platform=PlatformInfo(system="Linux", is_termux=False),
        )

    monkeypatch.setattr(shutil, "which", lambda name: None)

    run(probe=fake_probe)

    assert dict(os.environ) == snapshot, (
        "run() mutated os.environ; the Prereq check must be read-only"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CLI seam: prereqs.run() is wired into vicxiun.cli.main()
# ---------------------------------------------------------------------------


def test_cli_main_returns_nonzero_and_explains_when_ffmpeg_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``vicxiun`` with no args must run the Prereq check first."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("PATH", "")

    from vicxiun.cli import main

    code = main([])

    captured = capsys.readouterr()
    assert code != 0
    assert "ffmpeg" in captured.err
    assert "install" in captured.err.lower()


def test_cli_main_version_flag_works_even_when_a_prereq_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--version`` is how a user diagnoses a missing-program report; it
    must still print the version even when ffmpeg is absent.

    argparse's ``version`` action calls ``sys.exit(0)`` itself, so the
    Prereq check never runs and the user gets their version regardless
    of the machine state. We use ``pytest.raises(SystemExit)`` to assert
    the process exited cleanly.
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("PATH", "")

    from vicxiun.cli import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    captured = capsys.readouterr()
    assert excinfo.value.code == 0
    # argparse prints "vicxiun <version>" to stdout for --version.
    assert "vicxiun" in captured.out
    # No prereq nag should appear when the user only asked for --version.
    assert "ffmpeg" not in captured.err


def platform_info(*, system: str, is_termux: bool) -> PlatformInfo:
    return PlatformInfo(system=system, is_termux=is_termux)
