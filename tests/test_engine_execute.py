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
from vicxiun.plan import (
    CaptureKind,
    DownloadKind,
    Plan,
    Profile,
    profile_from_name,
)


def _make_plan(
    url: str = "https://example.com/v",
    profile: Profile | None = None,
) -> Plan:
    """A small Download Plan for unit tests; target is a stable path."""
    return Plan(
        url=url,
        kind=DownloadKind(),
        profile=profile if profile is not None else profile_from_name("phone-safe"),
        format_description="MP4 H.264 + AAC, up to 1080p",
        target_path=Path("/tmp/out/Some Channel/Title [abc].mp4"),
    )


def _make_capture_plan(url: str = "https://example.com/live") -> Plan:
    """A small Capture Plan for unit tests; target is a stable MKV path."""
    return Plan(
        url=url,
        kind=CaptureKind(),
        profile=profile_from_name("capture"),
        format_description="MKV, bestvideo+bestaudio until stopped",
        target_path=Path("/tmp/out/Some Channel/Title [abc].mkv"),
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
    """Ticket #8 acceptance: the telemetry flag is explicitly off."""
    opts = _safe_default_options()
    assert opts.get("enable_telemetry") is False


def test_safe_default_options_disable_cache() -> None:
    """ADR-0004: cache is disabled; no cache file is written."""
    opts = _safe_default_options()
    assert opts.get("cache") is False


# ---------------------------------------------------------------------------
# Executor option composition -- format, output template, capture options
# ---------------------------------------------------------------------------


def test_executor_options_use_the_profile_format_selector() -> None:
    plan = _make_plan()
    opts = _build_executor_options(plan, None)
    assert opts["format"] == plan.profile.format_selector


def test_executor_options_carry_the_profile_format_sort() -> None:
    """The Profile's sort reaches yt-dlp alongside the selector.

    The sort is where the Profile's real preferences live: the selector
    only says which kind of stream to take. Without it the 1080p cap and
    the codec preferences would simply not be applied (ticket #7).
    """
    plan = _make_plan()
    opts = _build_executor_options(plan, None)
    assert opts["format_sort"] == list(plan.profile.format_sort)
    # And for the default Profile that sort really does cap the height.
    assert "res:1080" in opts["format_sort"]


def test_executor_options_carry_the_profile_merge_container() -> None:
    """A Profile that names a container asks ffmpeg to produce it."""
    plan = _make_plan()
    opts = _build_executor_options(plan, None)
    assert opts["merge_output_format"] == plan.profile.merge_output_format


def test_executor_options_omit_an_unset_merge_container() -> None:
    """A Profile with no container preference leaves the key out.

    The ``audio`` Profile deliberately has none: naming MP3 would make
    ffmpeg re-encode a perfectly good Opus-in-WebM source for no reason.
    Leaving the key unset lets yt-dlp pick the compatible extension.
    """
    plan = _make_plan(profile=profile_from_name("audio"))
    opts = _build_executor_options(plan, None)
    assert "merge_output_format" not in opts


def test_executor_options_pin_the_target_path() -> None:
    plan = _make_plan()
    opts = _build_executor_options(plan, None)
    assert opts["outtmpl"] == str(plan.target_path)


def test_executor_options_for_a_capture_use_live_from_start() -> None:
    """A Capture Plan sets ``live_from_start`` so yt-dlp records from the
    earliest point the platform still holds -- the project's main use
    case (issue #9, acceptance criterion "Recording begins from the
    earliest point the platform still holds").
    """
    plan = _make_capture_plan()
    opts = _build_executor_options(plan, None)
    assert opts.get("live_from_start") is True


def test_executor_options_for_a_capture_use_hls_mpegts() -> None:
    """A Capture Plan sets ``hls_use_mpegts`` so the recording can be
    muxed into MKV with each fragment appended live. An interrupted
    MKV written this way remains playable; an interrupted MP4 does not
    (ADR-0007).
    """
    plan = _make_capture_plan()
    opts = _build_executor_options(plan, None)
    assert opts.get("hls_use_mpegts") is True


def test_executor_options_for_a_download_do_not_use_capture_flags() -> None:
    """A Download Plan must not carry the live-stream flags. The
    ``isinstance(kind, CaptureKind)`` discriminator must be the only
    branch that turns them on, otherwise a finished video would be
    recorded from "earliest available point" instead of downloaded.
    """
    plan = _make_plan()
    opts = _build_executor_options(plan, None)
    for key in ("live_from_start", "hls_use_mpegts"):
        assert key not in opts


def test_executor_options_install_progress_hooks_when_given() -> None:
    hooks = []

    def hook(d: dict[str, object]) -> None:
        hooks.append(d)

    opts = _build_executor_options(_make_plan(), hook)
    assert opts["progress_hooks"] == [hook]


def test_executor_options_silence_yt_dlps_own_progress_bar() -> None:
    """``noprogress`` is what stops yt-dlp drawing a bar of its own.

    yt-dlp chooses its printer from ``noprogress`` and ignores ``quiet``
    for this, so without it every progress line appeared twice: yt-dlp's
    bar on stdout plus the hook's line on stderr (ticket #30). Hooks must
    still be wired, or the progress printer and the disk-space guard
    would go silent too.
    """

    def hook(d: dict[str, object]) -> None:
        pass

    opts = _build_executor_options(_make_plan(), hook)
    assert opts["noprogress"] is True
    assert opts["progress_hooks"] == [hook]


def test_executor_options_carry_the_safety_defaults() -> None:
    """The executor must inherit the safety defaults so a regression in
    ``_build_executor_options`` cannot silently re-enable anything.

    Ticket #8 acceptance: update off, telemetry off, remote components
    off, cache off.
    """
    opts = _build_executor_options(_make_plan(), None)
    assert not opts.get("remote_components")
    assert opts.get("cache") is False
    assert opts.get("updater") is False
    assert opts.get("enable_telemetry") is False


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
        _translate_download_error(DownloadError("Sign in to confirm you are not a bot"))


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
