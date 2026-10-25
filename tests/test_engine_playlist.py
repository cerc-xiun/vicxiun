"""Tests for the engine's Playlist download path (ticket #23).

The contract under test:

* A Playlist downloads one file per entry, every entry named by
  vicxiun's own sanitiser inside the Playlist's folder -- yt-dlp's
  output templates are never used to name an entry (spec rule 43:
  hostile-title containment must hold for every file vicxiun writes).
* The Playlist is resolved flat first (the cheap extraction ticket #15
  introduced), then each entry is downloaded on its own with a fully
  literal ``outtmpl``.
* An entry the platform reports unavailable (``None``) is skipped; an
  entry with no link at all is a named failure, not a silent skip.
* An entry's ``DownloadError`` propagates to ``execute()``'s named-error
  translation, exactly as a single video's does.

``yt_dlp.YoutubeDL`` is monkeypatched with a recording fake, so no test
reaches the network.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from yt_dlp.utils import DownloadError

from vicxiun.engine import YtDlpEngine
from vicxiun.errors import PrerequisiteMissing, UserError
from vicxiun.plan import DownloadKind, Plan, profile_from_name


class FakeYoutubeDL:
    """Stands in for ``yt_dlp.YoutubeDL``, recording every construction.

    ``extract_results`` (a class attribute set per test) answers
    ``extract_info`` calls; ``download_error`` makes ``download`` raise.
    """

    extract_results: dict[str, object] = {}
    download_error: DownloadError | None = None
    created: list[FakeYoutubeDL] = []

    def __init__(self, options: dict[str, object]) -> None:
        self.options = options
        self.downloaded: list[list[str]] = []
        self.extracted: list[str] = []
        FakeYoutubeDL.created.append(self)

    def __enter__(self) -> FakeYoutubeDL:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def extract_info(self, url: str, download: bool, process: bool) -> object:
        self.extracted.append(url)
        assert download is False
        return FakeYoutubeDL.extract_results.get(url, {})

    def download(self, urls: list[str]) -> int:
        self.downloaded.append(list(urls))
        if FakeYoutubeDL.download_error is not None:
            raise FakeYoutubeDL.download_error
        return 0


@pytest.fixture(autouse=True)
def _fresh_fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    FakeYoutubeDL.extract_results = {}
    FakeYoutubeDL.download_error = None
    FakeYoutubeDL.created = []
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYoutubeDL)
    yield


def _playlist_plan(tmp_path: Path, count: int = 3) -> Plan:
    """A Playlist Plan: the target is the Playlist's output folder."""
    return Plan(
        url="https://example.com/mix",
        kind=DownloadKind(),
        profile=profile_from_name("phone-safe"),
        format_description="playlist of 3 Items",
        target_path=tmp_path / "Some Channel" / "A Big Mix [pl123]",
        playlist_count=count,
    )


def _entries() -> list[dict[str, object]]:
    return [
        {"title": "Entry One", "id": "e1", "url": "https://example.com/v1"},
        {"title": "Entry Two", "id": "e2", "url": "https://example.com/v2"},
        {"title": "Entry Three", "id": "e3", "url": "https://example.com/v3"},
    ]


def test_playlist_download_names_each_entry_inside_the_folder(
    tmp_path: Path,
) -> None:
    FakeYoutubeDL.extract_results = {
        "https://example.com/mix": {"entries": _entries()},
    }
    plan = _playlist_plan(tmp_path)

    result = YtDlpEngine().execute(plan)

    assert result == plan.target_path
    # One flat resolve of the Playlist itself, then one yt-dlp call per
    # entry.
    resolvers = [y for y in FakeYoutubeDL.created if y.extracted]
    assert len(resolvers) == 1
    assert resolvers[0].options.get("extract_flat") == "in_playlist"
    downloads = [y for y in FakeYoutubeDL.created if y.downloaded]
    assert len(downloads) == 3
    assert [y.downloaded[0][0] for y in downloads] == [
        "https://example.com/v1",
        "https://example.com/v2",
        "https://example.com/v3",
    ]
    folder = plan.target_path
    assert [y.options["outtmpl"] for y in downloads] == [
        str(folder / "Entry One [e1].mp4"),
        str(folder / "Entry Two [e2].mp4"),
        str(folder / "Entry Three [e3].mp4"),
    ]


def test_playlist_download_uses_a_literal_outtmpl_never_a_template(
    tmp_path: Path,
) -> None:
    """Spec rule 43: vicxiun chooses every filename. A yt-dlp output
    template (``%(title)s``) would let the engine pick names that
    bypass the hostile-title sanitiser -- the outtmpl handed to yt-dlp
    carries no template fields at all.
    """
    FakeYoutubeDL.extract_results = {
        "https://example.com/mix": {"entries": _entries()},
    }
    plan = _playlist_plan(tmp_path)

    YtDlpEngine().execute(plan)

    for ydl in FakeYoutubeDL.created:
        outtmpl = ydl.options.get("outtmpl")
        if outtmpl is not None:
            assert "%(" not in outtmpl
            assert Path(outtmpl).parent == plan.target_path.resolve()


def test_playlist_download_sanitises_a_hostile_entry_title(
    tmp_path: Path,
) -> None:
    FakeYoutubeDL.extract_results = {
        "https://example.com/mix": {
            "entries": [
                {
                    "title": "../../etc/passwd",
                    "id": "evil",
                    "url": "https://example.com/v1",
                }
            ],
        },
    }
    plan = _playlist_plan(tmp_path, count=1)

    YtDlpEngine().execute(plan)

    (ydl,) = [y for y in FakeYoutubeDL.created if y.downloaded]
    outtmpl = Path(str(ydl.options["outtmpl"])).resolve()
    assert outtmpl.is_relative_to(plan.target_path.resolve())
    assert "/" not in outtmpl.name and "\\" not in outtmpl.name


def test_playlist_download_skips_an_unavailable_entry(tmp_path: Path) -> None:
    FakeYoutubeDL.extract_results = {
        "https://example.com/mix": {
            "entries": [_entries()[0], None, _entries()[2]],
        },
    }
    plan = _playlist_plan(tmp_path)

    result = YtDlpEngine().execute(plan)

    assert result == plan.target_path
    downloads = [y for y in FakeYoutubeDL.created if y.downloaded]
    assert [y.downloaded[0][0] for y in downloads] == [
        "https://example.com/v1",
        "https://example.com/v3",
    ]


def test_playlist_download_refuses_an_entry_without_a_link(
    tmp_path: Path,
) -> None:
    FakeYoutubeDL.extract_results = {
        "https://example.com/mix": {
            "entries": [{"title": "Broken", "id": "e2"}],
        },
    }
    plan = _playlist_plan(tmp_path, count=1)

    with pytest.raises(UserError) as excinfo:
        YtDlpEngine().execute(plan)

    assert "entry 1 has no link" in str(excinfo.value)


def test_playlist_download_carries_the_progress_hook(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []

    def hook(d: dict[str, object]) -> None:
        events.append(d)

    FakeYoutubeDL.extract_results = {
        "https://example.com/mix": {"entries": _entries()[:1]},
    }
    plan = _playlist_plan(tmp_path, count=1)

    YtDlpEngine().execute(plan, hook)

    (ydl,) = [y for y in FakeYoutubeDL.created if y.downloaded]
    assert ydl.options["progress_hooks"] == [hook]
    # Per-entry options come from ``_build_executor_options`` too, so the
    # duplicate progress bar is silenced inside a Playlist as well
    # (ticket #30).
    assert ydl.options["noprogress"] is True


def test_playlist_download_maps_an_entry_error_like_a_single_video(
    tmp_path: Path,
) -> None:
    """A failing entry reaches ``execute()``'s translation seam: a
    yt-dlp ``DownloadError`` becomes the same named error a single
    video's failure would. The Playlist run stops at the entry that
    raised; a re-run lets yt-dlp skip the entries already on disk.
    """
    FakeYoutubeDL.extract_results = {
        "https://example.com/mix": {"entries": _entries()[:2]},
    }
    FakeYoutubeDL.download_error = DownloadError("ffmpeg not found")
    plan = _playlist_plan(tmp_path)

    with pytest.raises(PrerequisiteMissing):
        YtDlpEngine().execute(plan)

    downloads = [y for y in FakeYoutubeDL.created if y.downloaded]
    # The first entry was attempted on its own; the error stopped the
    # run before the second.
    assert [y.downloaded[0][0] for y in downloads] == ["https://example.com/v1"]
