"""Tests for the Profile table and what it actually selects (ticket #7).

Two layers:

* The Profile-to-options mapping is a pure function and is unit-tested
  exhaustively. Every Profile's contract is asserted directly: the
  phone-safe cap, the absence of a cap under ``best``, audio-only under
  ``audio``, and MKV for a Capture (ADR-0007).
* The *choice* is verified offline. A synthetic format list is fed
  through yt-dlp's real ``sort_formats`` and ``build_format_selector``
  against a stub ``YoutubeDL`` -- in-process, with no network and no
  download -- proving that H.264 + AAC at 1080p or below is selected
  when it exists, that the cap is dropped under ``best``, that
  ``audio`` yields no video stream, and that a list with only VP9 and
  Opus still produces a file.

The second layer is the point of the ticket. Asserting that the option
strings *look* right proves nothing about what yt-dlp does with them;
only running yt-dlp's own sorter does.
"""

from __future__ import annotations

from typing import Any

import pytest

from vicxiun.errors import UserError
from vicxiun.plan import Profile, profile_from_name

# ---------------------------------------------------------------------------
# The pure mapping
# ---------------------------------------------------------------------------

ALL_NAMES = ("phone-safe", "best", "audio", "capture")


@pytest.mark.parametrize("name", ALL_NAMES)
def test_every_named_profile_resolves(name: str) -> None:
    assert isinstance(profile_from_name(name), Profile)


@pytest.mark.parametrize("name", ALL_NAMES)
def test_profile_lookup_is_stable(name: str) -> None:
    """The mapping is a pure function of the name."""
    assert profile_from_name(name) == profile_from_name(name)
    assert profile_from_name(name) is profile_from_name(name)


def test_unknown_profile_name_is_a_user_error() -> None:
    with pytest.raises(UserError) as excinfo:
        profile_from_name("not-a-real-profile")

    # The message names the offenders and lists what is available, so a
    # user who typed the wrong word is not left guessing.
    assert "not-a-real-profile" in str(excinfo.value)
    for name in ALL_NAMES:
        assert name in str(excinfo.value)


def test_phone_safe_caps_resolution_at_1080() -> None:
    """Rule 9: the phone-safe format options cap height.

    The cap is a sort preference (``res:1080``) rather than a hard
    ``[height<=1080]`` filter, so it demotes taller formats without
    making them un-selectable.
    """
    sort = profile_from_name("phone-safe").format_sort
    assert "res:1080" in sort
    # 1080 is the cap: not 720, not 2160.
    assert sort[0] == "res:1080"


def test_phone_safe_prefers_h264_and_aac_and_muxes_mp4() -> None:
    profile = profile_from_name("phone-safe")
    assert "vcodec:avc" in profile.format_sort
    assert "acodec:aac" in profile.format_sort
    assert profile.merge_output_format == "mp4"
    assert profile.container == "mp4"


def test_phone_safe_uses_no_hard_filter_syntax() -> None:
    """No Profile selector may hard-require a codec or a resolution.

    This is the acceptance criterion in the negative: a selector
    containing ``[`` filters would exclude the very formats the sort
    exists to demote, so a video with no H.264 stream would fail
    instead of degrading. Guard the whole table, not just phone-safe.
    """
    for name in ALL_NAMES:
        selector = profile_from_name(name).format_selector
        assert "[" not in selector, (name, selector)
        assert "]" not in selector, (name, selector)


def test_best_has_no_resolution_cap() -> None:
    """``--best`` removes the cap and takes the highest quality."""
    profile = profile_from_name("best")
    for field in profile.format_sort:
        assert not field.startswith("res:"), field
    # No height limit at all, in any form.
    assert "height" not in " ".join(profile.format_sort)


def test_audio_selector_cannot_pick_video() -> None:
    """``--audio`` produces audio only."""
    profile = profile_from_name("audio")
    assert "bestaudio" in profile.format_selector
    assert "bestvideo" not in profile.format_selector


def test_capture_records_to_mkv() -> None:
    """A Capture records to MKV (ADR-0007)."""
    profile = profile_from_name("capture")
    assert profile.container == "mkv"
    assert profile.merge_output_format == "mkv"


def test_capture_and_best_agree_on_container() -> None:
    """Both unconstrained Profiles land in MKV.

    phone-safe is the only Profile that forces MP4; the other two that
    name a container name MKV, because both are cases where fidelity
    matters more than phone compatibility.
    """
    assert profile_from_name("capture").container == "mkv"
    assert profile_from_name("best").container == "mkv"
    assert profile_from_name("phone-safe").container == "mp4"


# ---------------------------------------------------------------------------
# Offline verification through yt-dlp's real sorting logic
# ---------------------------------------------------------------------------
#
# The Profile's options are applied exactly as the engine applies them,
# and yt-dlp's own code decides the winner. The stub replaces only the
# parts of YoutubeDL that touch the filesystem or the console; the
# sorter and the selector are the genuine article.

from yt_dlp.YoutubeDL import YoutubeDL


class _StubYDL(YoutubeDL):
    """A ``YoutubeDL`` that carries params but does no I/O.

    ``YoutubeDL.__init__`` creates directories, opens files and prints
    progress, none of which a unit test wants. Skipping it entirely and
    supplying just ``params`` is enough for ``sort_formats`` and
    ``build_format_selector``, which are the two pieces under test.
    """

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = dict(params)

    def to_screen(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def write_debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def deprecated_feature(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def report_warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _video(
    format_id: str,
    *,
    height: int,
    vcodec: str,
    ext: str = "mp4",
    tbr: int = 3000,
) -> dict[str, Any]:
    """One video-only stream, shaped like a real extractor's output."""
    return {
        "format_id": format_id,
        "ext": ext,
        "height": height,
        "width": height * 16 // 9,
        "fps": 30,
        "vcodec": vcodec,
        "acodec": "none",
        "tbr": tbr,
        "vbr": tbr,
        "abr": 0,
        "protocol": "https",
        "filesize": 100_000_000,
        "filesize_approx": 100_000_000,
    }


def _audio(
    format_id: str,
    *,
    acodec: str,
    ext: str,
    abr: int = 128,
) -> dict[str, Any]:
    """One audio-only stream."""
    return {
        "format_id": format_id,
        "ext": ext,
        "height": None,
        "width": None,
        "fps": None,
        "vcodec": "none",
        "acodec": acodec,
        "tbr": abr,
        "vbr": 0,
        "abr": abr,
        "protocol": "https",
        "filesize": 5_000_000,
        "filesize_approx": 5_000_000,
    }


# A realistic YouTube-shaped list: two AVC video streams at 1080p, a VP9
# alternative, a 2160p stream above the cap, and both an AAC and an Opus
# audio stream.
_TYPICAL: list[dict[str, Any]] = [
    _video("234", height=1080, vcodec="avc1.640028", tbr=3500),
    _video("137", height=1080, vcodec="avc1.640028", tbr=4500),
    _video("248", height=1080, vcodec="vp9", ext="webm", tbr=2500),
    _video("401", height=2160, vcodec="avc1.640033", tbr=14000),
    _video("135", height=720, vcodec="avc1.640028", tbr=1500),
    _audio("140", acodec="mp4a.40.2", ext="m4a", abr=128),
    _audio("251", acodec="opus", ext="webm", abr=160),
]

# The same video with no H.264 stream at all -- the "unusual video" case
# that a hard filter would reject outright.
_NO_H264: list[dict[str, Any]] = [
    _video("248", height=1080, vcodec="vp9", ext="webm", tbr=2500),
    _video("337", height=2160, vcodec="vp9", ext="webm", tbr=14000),
    _audio("251", acodec="opus", ext="webm", abr=160),
]


def _select(
    profile_name: str,
    formats: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply a Profile's options to ``formats`` and return what yt-dlp picks.

    Mirrors the engine: build the yt-dlp options from the Profile, sort
    the formats, then run the Profile's selector over them.
    """
    profile = profile_from_name(profile_name)
    options: dict[str, Any] = {
        "format": profile.format_selector,
        "format_sort": list(profile.format_sort),
        "prefer_free_formats": False,
    }
    if profile.merge_output_format:
        options["merge_output_format"] = profile.merge_output_format

    ydl = _StubYDL(options)
    ydl.sort_formats({"formats": formats})
    selector = ydl.build_format_selector(profile.format_selector)
    return ydl._select_formats(formats, selector)


def _streams(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten a selection into the individual streams that were chosen.

    A merged ``video+audio`` selection arrives as one entry carrying a
    ``requested_formats`` list; a single-format selection arrives as one
    bare entry. This returns the streams either way, which is the level
    the assertions are about.
    """
    streams: list[dict[str, Any]] = []
    for entry in selected:
        if "requested_formats" in entry:
            streams.extend(entry["requested_formats"])
        else:
            streams.append(entry)
    return streams


def _video_streams(streams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in streams if s.get("vcodec") not in (None, "none")]


def _audio_streams(streams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in streams if s.get("acodec") not in (None, "none")]


def test_phone_safe_selects_h264_aac_at_1080p_or_below() -> None:
    """The headline criterion: H.264 + AAC, 1080p or below, when available."""
    streams = _streams(_select("phone-safe", _TYPICAL))

    videos = _video_streams(streams)
    audios = _audio_streams(streams)
    assert len(videos) == 1, videos
    assert len(audios) == 1, audios

    assert videos[0]["vcodec"].startswith("avc1"), videos[0]
    assert videos[0]["height"] <= 1080, videos[0]
    assert audios[0]["acodec"].startswith("mp4a"), audios[0]


def test_phone_safe_does_not_reach_for_4k() -> None:
    """The 2160p stream is over the cap and is not chosen."""
    videos = _video_streams(_streams(_select("phone-safe", _TYPICAL)))
    assert all(v["height"] <= 1080 for v in videos), videos


def test_phone_safe_prefers_avc_over_vp9_at_the_same_height() -> None:
    """At an equal 1080p, the H.264 stream wins over the VP9 one."""
    videos = _video_streams(_streams(_select("phone-safe", _TYPICAL)))
    assert videos[0]["vcodec"].startswith("avc1"), videos[0]


def test_phone_safe_prefers_aac_over_opus() -> None:
    """At the same role, the AAC stream wins over the Opus one."""
    audios = _audio_streams(_streams(_select("phone-safe", _TYPICAL)))
    assert audios[0]["acodec"].startswith("mp4a"), audios[0]


def test_phone_safe_degrades_when_no_h264_stream_exists() -> None:
    """No H.264 anywhere: still a file, still capped, still sound.

    This is the criterion that hard filter syntax would fail. With
    ``[vcodec^=avc1]`` in the selector this Item would produce an error;
    with the sort it produces 1080p VP9 plus Opus audio.
    """
    streams = _streams(_select("phone-safe", _NO_H264))

    videos = _video_streams(streams)
    audios = _audio_streams(streams)
    assert videos, "expected a fallback video stream, not a failure"
    assert audios, "expected a fallback audio stream, not a failure"
    assert videos[0]["vcodec"].startswith("vp9"), videos[0]
    # The cap still holds: the codec preference degrades, the resolution
    # cap does not.
    assert videos[0]["height"] == 1080, videos[0]


def test_best_reaches_above_the_phone_safe_cap() -> None:
    """``--best`` takes the tallest stream available."""
    videos = _video_streams(_streams(_select("best", _TYPICAL)))
    assert videos, "expected a video stream"
    assert videos[0]["height"] == 2160, videos[0]


def test_audio_selects_no_video_stream() -> None:
    """``--audio`` returns audio and nothing else."""
    streams = _streams(_select("audio", _TYPICAL))
    assert _video_streams(streams) == [], streams
    assert len(_audio_streams(streams)) == 1, streams


def test_audio_prefers_aac() -> None:
    audios = _audio_streams(_streams(_select("audio", _TYPICAL)))
    assert audios[0]["acodec"].startswith("mp4a"), audios[0]


def test_capture_selects_the_highest_stream_without_a_cap() -> None:
    """A Capture records the best on offer; it is not phone-bound."""
    videos = _video_streams(_streams(_select("capture", _TYPICAL)))
    assert videos, "expected a video stream"
    assert videos[0]["height"] == 2160, videos[0]


def test_capture_degrades_when_no_h264_stream_exists() -> None:
    """A VP9-only live stream is still capturable."""
    streams = _streams(_select("capture", _NO_H264))
    assert _video_streams(streams), "expected a video stream"
    assert _audio_streams(streams), "expected an audio stream"


def test_no_profile_relies_on_a_pre_merged_format() -> None:
    """No selector may be bare ``best`` or ``worst``.

    In yt-dlp, a bare ``b``/``best`` is defined as a format that already
    carries both picture and sound. It matches nothing on a source that
    publishes separate streams, which is the usual shape of a live
    stream, so a Capture using it would find no format at all. Every
    Profile that wants "the best available" has to say
    ``bestvideo*+bestaudio/best`` so the separate streams are eligible.
    """
    for name in ALL_NAMES:
        selector = profile_from_name(name).format_selector
        assert selector not in {"best", "b", "worst", "w"}, (name, selector)


@pytest.mark.parametrize("name", ALL_NAMES)
def test_every_profile_yields_a_stream_from_the_typical_list(name: str) -> None:
    """No Profile may select nothing from an ordinary video.

    Guards the whole table against a selector that matches no format in
    a realistic list -- the failure mode a typo in ``-f`` syntax causes.
    """
    selected = _select(name, _TYPICAL)
    assert selected, f"{name} selected nothing from the typical list"
    streams = _streams(selected)
    assert _video_streams(streams) or _audio_streams(streams), (name, streams)
