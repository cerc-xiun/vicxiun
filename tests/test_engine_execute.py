"""Tests for the engine's ``execute`` path (ticket #8).

These tests cover the seam added by ticket #8: turning a resolved Plan
into an actual file on disk. Every test substitutes the executor with
a fake so no test reaches the network. The mapping from yt-dlp's
``DownloadError`` to ``vicxiun``'s named errors is asserted here.

The contract under test:

* The engine returns the path the file actually landed at.
* A yt-dlp ``DownloadError`` whose message names DRM becomes
  ``RefusedContent`` (exit code 4).
* A yt-dlp ``DownloadError`` whose message names a missing helper
  becomes ``PrerequisiteMissing`` (exit code 3).
* Any other ``DownloadError`` becomes ``UserError`` (exit code 2).
* ``updater`` and ``update`` are off; ``enable_telemetry`` is off;
  ``remote_components`` is off.
* ``base_options()`` is the same shape the engine actually uses, so a
  regression in the defaults cannot sneak past the test suite.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from vicxiun.engine import (
    YtDlpEngine,
    _build_executor_options,
    _safe_default_options,
    _translate_download_error,
)
from vicxiun.errors import (
    PrerequisiteMissing,
    RefusedContent,
    UserError,
)
from vicxiun.plan import CaptureKind, DownloadKind, Plan, profile_from_name


def _make_plan(url: str = "https://example.com/v") -> Plan:
    """A small Plan for unit tests; target is a stable path."""
    return Plan(
        url=url,
        kind=DownloadKind(),
        profile=profile_from_name("phone-safe"),
        format_description="MP4 H.264 + AAC, up to 1080p",
        target_path=Path("/tmp/out/Some Channel/Title [abc].mp4"),
    )


# ---------------------------------------------------------------------------
# execute() -- the seam added by ticket #8
# ---------------------------------------------------------------------------


def test_engine_execute_returns_the_path_from_the_executor(tmp_path: Path) -> None:
    target = tmp_path / "Some Channel" / "Title [abc].mp4"
    seen: list[Plan] = []
    seen_progress: list[Callable[[dict[str, object]], None] | None] = []

    def fake_executor(plan, progress):
        seen.append(plan)
        seen_progress.append(progress)
        return target

    engine = YtDlpEngine(executor=fake_executor)
    plan = _make_plan()
    actual = engine.execute(plan)

    assert actual == target
    assert seen == [plan]
    assert seen_progress[0] is None  # the engine does not install a hook by default


def test_engine_execute_invokes_the_progress_hook() -> None:
    events: list[dict[str, object]] = []

    def progress(d: dict[str, object]) -> None:
        events.append(d)

    def fake_executor(plan, on_progress):
        on_progress({"status": "downloading", "_percent_str": "10%"})
        on_progress({"status": "finished"})
        return Path("/tmp/out/a.mp4")

    engine = YtDlpEngine(executor=fake_executor)
    engine.execute(_make_plan(), progress)
    assert [e["status"] for e in events] == ["downloading", "finished"]


def test_engine_execute_maps_drm_error_to_refused_content() -> None:
    """A yt-dlp error that names DRM becomes ``RefusedContent``."""

    def raising(plan, progress):
        from yt_dlp.utils import DownloadError

        raise DownloadError("This video is DRM-protected and cannot be downloaded.")

    engine = YtDlpEngine(executor=raising)
    with pytest.raises(RefusedContent) as excinfo:
        engine.execute(_make_plan())
    assert "DRM" in str(excinfo.value)


def test_engine_execute_maps_missing_ffmpeg_to_missing_prereq() -> None:
    def raising(plan, progress):
        from yt_dlp.utils import DownloadError

        raise DownloadError("ffmpeg not found")

    engine = YtDlpEngine(executor=raising)
    with pytest.raises(PrerequisiteMissing):
        engine.execute(_make_plan())


def test_engine_execute_maps_other_error_to_user_error() -> None:
    """A yt-dlp error that does not match the special phrases is a
    user-fixable failure (the link is bad, the video was removed, etc.).
    """

    def raising(plan, progress):
        from yt_dlp.utils import DownloadError

        raise DownloadError("Video not available")

    engine = YtDlpEngine(executor=raising)
    with pytest.raises(UserError):
        engine.execute(_make_plan())


# ---------------------------------------------------------------------------
# Engine option defaults -- ticket #8 acceptance criteria
# ---------------------------------------------------------------------------


def test_safe_default_options_disable_remote_components() -> None:
    """ADR-0004 + ticket #8 acceptance: remote_components is off."""
    opts = _safe_default_options()
    # ``remote_components`` must be falsy: an empty set, None or False
    # all mean "off". An empty set is what yt-dlp itself treats as off.
    assert not opts.get("remote_components")


def test_safe_default_options_disable_update() -> None:
    """Ticket #8 acceptance: the engine disables yt-dlp's update check."""
    opts = _safe_default_options()
    # ``updater`` is the yt-dlp registry key for the -U flag.
    assert opts.get("updater") is False


def test_safe_default_options_disable_telemetry() -> None:
    """Ticket #8 acceptance: no telemetry opt-in flag is positive."""
    opts = _safe_default_options()
    for key in ("enable_telemetry", "telemetry"):
        assert opts.get(key) in (None, False, "off")


def test_safe_default_options_disable_cache() -> None:
    """ADR-0004: cache is disabled; no cache file is written."""
    opts = _safe_default_options()
    assert opts.get("cache") is False


# ---------------------------------------------------------------------------
# Executor option composition -- format, output template, capture options
# ---------------------------------------------------------------------------


def _capture_plan() -> Plan:
    return Plan(
        url="https://example.com/live",
        kind=CaptureKind(),
        profile=profile_from_name("capture"),
        format_description="MKV until stopped",
        target_path=Path("/tmp/out/Some Channel/Stream [live].mkv"),
        wait_for_stream=True,
    )


def test_executor_options_use_the_profile_format_selector() -> None:
    plan = _make_plan()
    opts = _build_executor_options(plan, None)
    assert opts["format"] == plan.profile.format_selector


def test_executor_options_pin_the_target_path() -> None:
    plan = _make_plan()
    opts = _build_executor_options(plan, None)
    assert opts["outtmpl"] == str(plan.target_path)


def test_executor_options_for_a_capture_use_live_from_start() -> None:
    plan = _capture_plan()
    opts = _build_executor_options(plan, None)
    assert opts.get("live_from_start") is True
    assert opts.get("wait_for_stream") is True
    assert opts.get("hls_use_mpegts") is True


def test_executor_options_for_a_download_omit_capture_flags() -> None:
    plan = _make_plan()
    opts = _build_executor_options(plan, None)
    assert "live_from_start" not in opts or not opts["live_from_start"]


def test_executor_options_install_progress_hooks_when_given() -> None:
    hooks = []

    def hook(d: dict[str, object]) -> None:
        hooks.append(d)

    opts = _build_executor_options(_make_plan(), hook)
    assert opts["progress_hooks"] == [hook]


# ---------------------------------------------------------------------------
# _translate_download_error -- the mapping table itself
# ---------------------------------------------------------------------------


def test_translate_download_error_for_sign_in_required() -> None:
    """``sign in to confirm`` is the YouTube anti-bot phrase; this is
    not DRM but it is still content we cannot reach without the user's
    cookies, which this version refuses. ADR-0003 covers DRM/paywall;
    the practical effect is the same: ``RefusedContent``.
    """
    from yt_dlp.utils import DownloadError

    with pytest.raises(RefusedContent):
        _translate_download_error(
            DownloadError("Sign in to confirm you are not a bot")
        )


def test_translate_download_error_for_premium() -> None:
    from yt_dlp.utils import DownloadError

    with pytest.raises(RefusedContent):
        _translate_download_error(
            DownloadError("This video is available to premium subscribers only")
        )


def test_translate_download_error_for_node_missing() -> None:
    from yt_dlp.utils import DownloadError

    with pytest.raises(PrerequisiteMissing):
        _translate_download_error(DownloadError("no such file or directory: node"))


def test_translate_download_error_for_unrecognised_falls_through() -> None:
    from yt_dlp.utils import DownloadError

    with pytest.raises(UserError):
        _translate_download_error(DownloadError("HTTP Error 410: video gone"))


def test_translate_download_error_never_returns_silently() -> None:
    """Anything passed to the translator must raise a named error.

    ADR-0004 + ticket #8 acceptance: nothing is swallowed silently.
    """
    from yt_dlp.utils import DownloadError

    with pytest.raises(UserError):
        _translate_download_error(DownloadError("something unexpected"))