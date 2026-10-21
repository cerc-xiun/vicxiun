"""Tests for the planning layer.

A Plan describes what the tool intends to do with one Item: its kind
(Download or Capture), its Profile, the chosen format and the absolute
target path. The real run prints *and executes* it; ``--dry-run`` prints it
and stops. The Plan is produced by the same code path the real run uses --
this module's tests pin that seam.

The engine is injected (``MetadataResolver``), so the network is never
reached from the test suite.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from vicxiun.errors import UserError
from vicxiun.plan import (
    CaptureKind,
    DownloadKind,
    InvalidLinkError,
    Plan,
    Profile,
    profile_from_name,
    resolve_plan,
)


def PhoneSafeProfile() -> Profile:
    """Convenience: return the default 'phone-safe' profile for tests."""
    return profile_from_name("phone-safe")


# ---------------------------------------------------------------------------
# Fake metadata resolver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeMetadata:
    """Shape that mirrors what the engine yields from a metadata fetch."""

    title: str
    uploader: str
    ext: str
    is_live: bool
    format_description: str
    video_id: str
    live_status: str = ""
    height: int | None = None
    vcodec: str | None = None
    acodec: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "uploader": self.uploader,
            "ext": self.ext,
            "is_live": self.is_live,
            "live_status": self.live_status,
            "format_description": self.format_description,
            "video_id": self.video_id,
            "height": self.height,
            "vcodec": self.vcodec,
            "acodec": self.acodec,
        }


class _FakeResolver:
    """Resolves a URL to metadata without touching the network."""

    def __init__(self, mapping: Mapping[str, _FakeMetadata]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict[str, object]:
        self.calls.append(url)
        if url not in self.mapping:
            raise InvalidLinkError(f"unresolvable: {url}")
        return self.mapping[url].to_dict()


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=abc",
        "http://example.com/foo",
        "HTTPS://EXAMPLE.COM/WATCH",  # scheme is case-insensitive
    ],
)
def test_resolve_plan_accepts_http_and_https(url: str) -> None:
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="t",
                uploader="u",
                ext="mp4",
                is_live=False,
                format_description="MP4 1080p",
                video_id="id1",
            )
        }
    )
    plan = resolve_plan(
        url,
        profile=PhoneSafeProfile(),
        output_root=Path("/out"),
        resolver=resolver,
    )
    assert isinstance(plan.kind, DownloadKind)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/foo",
        "-rf",
        "--verbose",
        "-",
        "javascript:alert(1)",
        "",
    ],
)
def test_resolve_plan_rejects_non_http(url: str) -> None:
    resolver = _FakeResolver({})
    with pytest.raises(UserError):
        resolve_plan(
            url,
            profile=PhoneSafeProfile(),
            output_root=Path("/out"),
            resolver=resolver,
        )


def test_resolve_plan_rejects_shell_metacharacters() -> None:
    resolver = _FakeResolver({})
    for host in (
        "https://ex.com/$(touch x)",
        "https://ex.com/`id`",
        "https://ex.com/x;rm -rf /",
        "https://ex.com/ | nc evil 1",
    ):
        with pytest.raises(UserError):
            resolve_plan(
                host,
                profile=PhoneSafeProfile(),
                output_root=Path("/out"),
                resolver=resolver,
            )


def test_resolve_plan_does_not_treat_dash_prefix_as_flag(tmp_path: Path) -> None:
    """The argument after --dry-run must not be parsed as a flag even when
    it starts with ``-``. The CLI layer rejects such links with UserError."""
    resolver = _FakeResolver({})
    with pytest.raises(UserError):
        resolve_plan(
            "--surprise",
            profile=PhoneSafeProfile(),
            output_root=tmp_path,
            resolver=resolver,
        )


# ---------------------------------------------------------------------------
# Kind classification
# ---------------------------------------------------------------------------


def test_resolve_plan_classifies_finished_video_as_download(tmp_path: Path) -> None:
    url = "https://example.com/fin"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Talk",
                uploader="Chan",
                ext="mp4",
                is_live=False,
                format_description="MP4 1080p",
                video_id="id",
            )
        }
    )
    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert isinstance(plan.kind, DownloadKind)
    assert plan.kind.is_live is False


def test_resolve_plan_classifies_live_stream_as_capture(tmp_path: Path) -> None:
    url = "https://example.com/live"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Live",
                uploader="Chan",
                ext="mkv",
                is_live=True,
                format_description="MKV bestvideo+bestaudio",
                video_id="id2",
            )
        }
    )
    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert isinstance(plan.kind, CaptureKind)
    assert plan.kind.is_live is True


def test_capture_container_is_mkv(tmp_path: Path) -> None:
    url = "https://example.com/live"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Live",
                uploader="Chan",
                ext="mkv",
                is_live=True,
                format_description="MKV bestvideo+bestaudio",
                video_id="id2",
            )
        }
    )
    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert plan.target_path.suffix == ".mkv"


def test_download_container_is_mp4(tmp_path: Path) -> None:
    url = "https://example.com/fin"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Talk",
                uploader="Chan",
                ext="mp4",
                is_live=False,
                format_description="MP4 1080p",
                video_id="id",
            )
        }
    )
    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert plan.target_path.suffix == ".mp4"


# ---------------------------------------------------------------------------
# Target path
# ---------------------------------------------------------------------------


def test_plan_target_path_uses_output_root(tmp_path: Path) -> None:
    url = "https://example.com/fin"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Talk",
                uploader="Chan",
                ext="mp4",
                is_live=False,
                format_description="MP4 1080p",
                video_id="id",
            )
        }
    )
    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert plan.target_path.parent.parent.resolve() == tmp_path.resolve()


def test_plan_target_path_uses_unknown_uploader_when_missing(tmp_path: Path) -> None:
    url = "https://example.com/anonymous"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Talk",
                uploader="",
                ext="mp4",
                is_live=False,
                format_description="MP4 1080p",
                video_id="id",
            )
        }
    )
    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert plan.target_path.parts[-2] == "_unknown"


def test_resolve_plan_for_a_playlist_targets_its_folder(tmp_path: Path) -> None:
    """A Playlist's Plan targets the Playlist's own output folder
    (ticket #23): the same name a single video of this link would take,
    minus the extension, and the executor puts one file per entry
    inside it.
    """
    url = "https://example.com/mix"

    def resolver(resolved: str) -> dict[str, object]:
        return {
            "_type": "playlist",
            "title": "A Big Mix",
            "uploader": "Chan",
            "id": "pl123",
            "playlist_count": 7,
        }

    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert isinstance(plan.kind, DownloadKind)
    assert plan.playlist_count == 7
    assert plan.target_path.parent.parent.resolve() == tmp_path.resolve()
    assert plan.target_path.parent.name == "Chan"
    assert plan.target_path.name == "A Big Mix [pl123]"
    assert plan.target_path.suffix == ""


# ---------------------------------------------------------------------------
# Profile / format description
# ---------------------------------------------------------------------------


def test_plan_records_profile_in_force(tmp_path: Path) -> None:
    url = "https://example.com/v"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Talk",
                uploader="Chan",
                ext="mp4",
                is_live=False,
                format_description="MP4 1080p",
                video_id="id",
            )
        }
    )
    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert isinstance(plan.profile, Profile)
    assert plan.profile.name == "phone-safe"


def test_plan_carries_the_format_description(tmp_path: Path) -> None:
    url = "https://example.com/v"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Talk",
                uploader="Chan",
                ext="mp4",
                is_live=False,
                format_description="MP4, H.264 video + AAC audio, up to 1080p",
                video_id="id",
            )
        }
    )
    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert "MP4" in plan.format_description
    assert "1080p" in plan.format_description


def test_plan_carries_the_link(tmp_path: Path) -> None:
    url = "https://example.com/v"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Talk",
                uploader="Chan",
                ext="mp4",
                is_live=False,
                format_description="MP4",
                video_id="id",
            )
        }
    )
    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert plan.url == url


# ---------------------------------------------------------------------------
# Wait-for-stream reporting
# ---------------------------------------------------------------------------


def test_plan_records_wait_for_stream_when_live(tmp_path: Path) -> None:
    url = "https://example.com/live"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Live",
                uploader="Chan",
                ext="mkv",
                is_live=True,
                format_description="MKV",
                video_id="id2",
            )
        }
    )
    plan = resolve_plan(
        url,
        PhoneSafeProfile(),
        tmp_path,
        resolver,
        wait_for_stream=True,
    )
    assert plan.wait_for_stream is True


def test_plan_does_not_wait_on_finished_video(tmp_path: Path) -> None:
    url = "https://example.com/fin"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Talk",
                uploader="Chan",
                ext="mp4",
                is_live=False,
                format_description="MP4",
                video_id="id",
            )
        }
    )
    plan = resolve_plan(
        url,
        PhoneSafeProfile(),
        tmp_path,
        resolver,
        wait_for_stream=True,
    )
    assert plan.wait_for_stream is False


# A scheduled stream: the platform says a live stream is coming
# (yt-dlp's ``live_status="is_upcoming"``) but it has not started yet.


def test_plan_waits_on_a_scheduled_stream_when_asked(tmp_path: Path) -> None:
    """AC (ticket #11): --wait-for-stream on a not-yet-live stream waits.

    The Item stays a Capture even though the stream is not live yet,
    so the Queue waits for it and records it when it starts.
    """
    url = "https://example.com/scheduled"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Tonight's Stream",
                uploader="Chan",
                ext="mkv",
                is_live=False,
                live_status="is_upcoming",
                format_description="MKV",
                video_id="id3",
            )
        }
    )
    plan = resolve_plan(
        url,
        PhoneSafeProfile(),
        tmp_path,
        resolver,
        wait_for_stream=True,
    )
    assert isinstance(plan.kind, CaptureKind)
    assert plan.kind.is_live is False
    assert plan.wait_for_stream is True


def test_plan_ignores_a_scheduled_stream_without_the_flag(tmp_path: Path) -> None:
    """Without --wait-for-stream nothing changed (ticket #11).

    A scheduled link resolves exactly as before ticket #11: a
    Download, no waiting.
    """
    url = "https://example.com/scheduled"
    resolver = _FakeResolver(
        {
            url: _FakeMetadata(
                title="Tonight's Stream",
                uploader="Chan",
                ext="mkv",
                is_live=False,
                live_status="is_upcoming",
                format_description="MKV",
                video_id="id3",
            )
        }
    )
    plan = resolve_plan(url, PhoneSafeProfile(), tmp_path, resolver)
    assert isinstance(plan.kind, DownloadKind)
    assert plan.wait_for_stream is False


# ---------------------------------------------------------------------------
# Plan rendering
# ---------------------------------------------------------------------------


def test_plan_render_includes_required_fields() -> None:
    plan = Plan(
        url="https://example.com/v",
        kind=DownloadKind(),
        profile=PhoneSafeProfile(),
        format_description="MP4, H.264 + AAC, up to 1080p",
        target_path=Path("/out/Chan/Talk [id].mp4"),
        wait_for_stream=False,
    )
    text = plan.render()
    assert "https://example.com/v" in text
    assert "Download" in text
    assert "phone-safe" in text
    assert "MP4" in text
    assert "1080p" in text
    assert "Chan" in text
    assert "Talk" in text


def test_plan_render_for_capture_says_capture() -> None:
    plan = Plan(
        url="https://example.com/live",
        kind=CaptureKind(),
        profile=PhoneSafeProfile(),
        format_description="MKV, bestvideo+bestaudio",
        target_path=Path("/out/Chan/Live [id2].mkv"),
        wait_for_stream=True,
    )
    text = plan.render()
    assert "Capture" in text
    assert "would wait for the stream to start" in text


def test_plan_render_for_download_says_no_wait() -> None:
    plan = Plan(
        url="https://example.com/v",
        kind=DownloadKind(),
        profile=PhoneSafeProfile(),
        format_description="MP4, 1080p",
        target_path=Path("/out/Chan/Talk [id].mp4"),
        wait_for_stream=False,
    )
    text = plan.render()
    assert "would not wait" in text or "not wait" in text


# ItemKind total coverage -- DownloadKind and CaptureKind are distinct
# classes; the renderer treats them differently.


def test_plan_to_json_roundtrips() -> None:
    plan = Plan(
        url="https://example.com/v",
        kind=DownloadKind(),
        profile=PhoneSafeProfile(),
        format_description="MP4",
        target_path=Path("/out/Chan/Talk [id].mp4"),
        wait_for_stream=False,
    )
    encoded = plan.to_json()
    assert isinstance(encoded, str)
    payload = json.loads(encoded)
    assert payload["url"] == "https://example.com/v"
    assert payload["kind"] == "Download"
    assert payload["profile"] == "phone-safe"
