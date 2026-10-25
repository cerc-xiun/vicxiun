"""Planning layer: resolve a link into a concrete plan without downloading.

The Plan is the contract between "resolve" and "execute": the same code
path produces the Plan for both ``vicxiun`` and ``vicxiun --dry-run``. The
real run then executes it; the preview prints it and stops.

The plan is *not* a parallel implementation -- it sits exactly on the
execution path. The engine adapter is injected so tests never reach the
network. Two child kinds -- ``Download`` and ``Capture`` -- are decided by
inspecting the link (``is_live``, and ``live_status`` for a stream that
has not started yet).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from vicxiun.errors import UserError
from vicxiun.paths import resolve_target

# Recognise http and https URLs only. We accept the scheme case-insensitively
# (see RFC 3986) and refuse anything else -- ``file://``, ``ftp``, ``data``,
# shell wrappers, anything starting with ``-`` (which would be misread by the
# shell as a flag, and is rejected with the user-error code), and empty.
_HTTP_SCHEMES = re.compile(r"^https?://", re.IGNORECASE)

# yt-dlp's live-status value for a stream that has not started yet
# (the full vocabulary is ``is_live``, ``is_upcoming``, ``post_live``,
# ``not_live``). Sites that report nothing (or something else) are
# never treated as scheduled.
_UPCOMING = "is_upcoming"


class InvalidLinkError(UserError):
    """The link is not acceptable as a ``vicxiun`` Item."""


class MetadataError(UserError):
    """The link could not be resolved to metadata by the engine."""


def _validate_url(url: str) -> str:
    """Return the URL if it is an acceptable ``vicxiun`` link.

    A link starting with ``-`` is never a URL. The set of shell
    metacharacters is rejected wholesale -- we never quote our way to
    safety. ``file://`` and other non-http schemes are rejected.
    """
    if not isinstance(url, str) or not url:
        raise InvalidLinkError("a link is required")
    if url.startswith("-"):
        raise InvalidLinkError("a link cannot begin with '-'")
    # Shell metacharacters that suggest argument injection. ``&`` is not one
    # of them: vicxiun never invokes a shell (yt-dlp's Python API, guarded by
    # the source-level no-shell test), and ``&`` is how a URL carries a
    # second query parameter (``&list=``, ``&index=``, ``&t=``).
    shell_metas = ("$", "`", "|", ";", ">", "<", "\n", "\r", "\0")
    offending: list[str] = []
    for char in url:
        if char in shell_metas and char not in offending:
            offending.append(char)
    if offending:
        # ``repr`` of each character, never the raw one: a newline or NUL
        # must not be echoed into the terminal, and naming the character is
        # what makes the refusal actionable.
        named = ", ".join(repr(char) for char in offending)
        if len(offending) == 1:
            raise InvalidLinkError(f"a link contains an unsafe character: {named}")
        raise InvalidLinkError(f"a link contains unsafe characters: {named}")
    if not _HTTP_SCHEMES.match(url):
        raise InvalidLinkError(
            "only http:// and https:// links are accepted",
        )
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc == "" or not parsed.hostname:
        raise InvalidLinkError("the link has no host")
    return url


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    """A named choice of what an Item should produce.

    ``format_selector`` is the yt-dlp ``-f`` expression the real run
    would pass. ``format_description`` is a human-readable rendering of
    the same choice (codec/container/resolution) for ``--dry-run``.
    ``container`` is the file extension the real run produces.

    ``format_sort`` and ``merge_output_format`` are the two companions to
    the selector, and they carry most of the Profile's meaning. The
    selector names *which streams* to take; the sort says *which of the
    available streams are preferred*, in yt-dlp's own ordering language.

    Selection deliberately uses the sort and not hard filter syntax.
    A selector like ``bestvideo[vcodec^=avc1][height<=?1080]`` would
    exclude every video without an H.264 stream, so a perfectly ordinary
    upload with only VP9 would fail outright. ``res:1080``, ``vcodec:avc``
    and ``acodec:aac`` are limit-form sort fields: they *demote* the
    unwanted options to the back of the list without removing any, and
    yt-dlp's ``bestvideo``/``bestaudio`` read the list from the end. The
    result is that the wanted stream is chosen when it exists, and a
    playable one is chosen when it does not (ADR-0002).
    """

    name: str
    format_selector: str
    format_description: str
    container: str
    format_sort: tuple[str, ...] = ()
    merge_output_format: str = ""


_PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        Profile(
            name="phone-safe",
            # Separate video and audio where they exist; /best catches the
            # older uploads that only ship one merged file. The ``*`` lets
            # bestvideo take a stream that also carries audio, which keeps
            # a single-file fallback available.
            format_selector="bestvideo*+bestaudio/best",
            format_description="MP4, H.264 video + AAC audio, up to 1080p",
            container="mp4",
            # res:1080 demotes 1440p and 2160p without excluding them.
            # vcodec:avc and acodec:aac demote the codecs yt-dlp ranks
            # above AVC and AAC (AV1, VP9.2, VP9, HEVC) without excluding
            # them either. br breaks remaining ties the usual way.
            format_sort=("res:1080", "vcodec:avc", "acodec:aac", "br"),
            merge_output_format="mp4",
        ),
        Profile(
            name="best",
            format_selector="bestvideo*+bestaudio/best",
            format_description=("bestvideo + bestaudio in the source container"),
            container="mkv",
            # No height cap and no codec preference: the user asked for the
            # most the source has, so yt-dlp's own ordering is the answer.
            format_sort=(),
            merge_output_format="mkv",
        ),
        Profile(
            name="audio",
            format_selector="bestaudio/best",
            format_description="best audio stream",
            container="mp3",
            # AAC where it exists, highest bit rate otherwise. Opus is
            # demoted rather than excluded.
            format_sort=("acodec:aac", "abr"),
            # The merged container is left to ffmpeg's compatible-ext
            # choice, so a good Opus-in-WebM source is not re-encoded to
            # MP3 just to satisfy a container name.
            merge_output_format="",
        ),
        Profile(
            name="capture",
            # Not bare "best": in yt-dlp, "best" means a format that
            # already carries both picture and sound, and it matches
            # nothing at all on a live stream that publishes separate
            # streams -- which is most of them. The explicit merge plus
            # the /best fallback takes the separate streams when they
            # are there and a merged file when they are not.
            format_selector="bestvideo*+bestaudio/best",
            format_description=("bestvideo+bestaudio recorded as MKV until stopped"),
            container="mkv",
            # No cap and no codec preference: record what the stream
            # offers rather than what a phone would want.
            format_sort=(),
            merge_output_format="mkv",
        ),
    )
}


def profile_from_name(name: str) -> Profile:
    """Return the Profile named ``name``, or raise ``UserError``."""
    if name not in _PROFILES:
        raise UserError(
            f"unknown profile: {name!r}; expected one of {sorted(_PROFILES)}",
        )
    return _PROFILES[name]


# ---------------------------------------------------------------------------
# Item kind
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DownloadKind:
    is_live: bool = False
    name: str = "Download"


@dataclass(frozen=True)
class CaptureKind:
    is_live: bool = True
    name: str = "Capture"


ItemKind = DownloadKind | CaptureKind


# ---------------------------------------------------------------------------
# Metadata shape from the engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Metadata:
    """The metadata the engine yields from a non-downloading extract.

    ``live_status`` is yt-dlp's own vocabulary (``is_live``, ``is_upcoming``,
    ``post_live``, ``not_live``); empty when the site does not report it.
    A *scheduled* stream reports ``is_live=False`` with
    ``live_status="is_upcoming"`` -- that combination is what lets
    ``--wait-for-stream`` recognise a stream that has not started yet.
    """

    title: str
    uploader: str
    ext: str
    is_live: bool
    format_description: str
    video_id: str
    live_status: str = ""
    # A playlist (a link that resolves to many videos) reports how many
    # Items it holds; a plain video is not a playlist and reports 0
    # (ticket #15).
    playlist_count: int = 0


class MetadataResolver(Protocol):
    """Anything that resolves a URL to metadata without downloading media.

    The real implementation uses ``yt-dlp`` with ``download=False`` and
    the cache disabled. Tests substitute a fake.
    """

    def __call__(self, url: str) -> object: ...


def _to_metadata(raw: object) -> Metadata:
    """Coerce an engine dict (or test fake) into a ``Metadata``.

    A playlist (``_type == "playlist"``) is a link that resolves to many
    videos (ticket #15): it carries a ``playlist_count`` instead of the
    per-video fields, which are filled with the playlist's own values --
    the count is what the large-playlist confirmation needs, and the
    per-video shape is what ``resolve_plan`` and the paths module need
    to keep working unchanged.
    """
    if not isinstance(raw, dict):
        raise MetadataError("engine returned a non-mapping value")
    if raw.get("_type") == "playlist":
        # A playlist reports how many Items it holds (ticket #15). The
        # count must be known and truthful: it is what the confirmation
        # works from, and a silent zero would let a whole channel
        # download unconfirmed -- the exact harm ticket #15 exists to
        # prevent. yt-dlp reports ``playlist_count``; the flat entries
        # themselves are the fallback.
        count_raw = raw.get("playlist_count")
        if count_raw is None:
            entries = raw.get("entries")
            if entries is not None:
                try:
                    count_raw = len(entries)
                except TypeError:
                    count_raw = None
        if count_raw is None:
            raise MetadataError(
                "engine result is a playlist but does not say how many Items it holds"
            )
        try:
            count = int(count_raw)
        except (TypeError, ValueError) as exc:
            raise MetadataError(
                f"engine result has an unusable playlist count: {count_raw!r}"
            ) from exc
        if "title" not in raw:
            raise MetadataError("engine result missing keys: ['title']")
        return Metadata(
            title=str(raw["title"]),
            uploader=str(raw.get("uploader") or raw.get("channel") or ""),
            ext="",
            is_live=False,
            format_description=f"playlist of {count} Items",
            video_id=str(raw.get("id") or ""),
            playlist_count=count,
        )
    keys = (
        "title",
        "uploader",
        "ext",
        "is_live",
        "format_description",
        "video_id",
    )
    missing = [k for k in keys if k not in raw]
    if missing:
        raise MetadataError(f"engine result missing keys: {missing}")
    return Metadata(
        title=str(raw["title"]),
        uploader=str(raw["uploader"]),
        ext=str(raw["ext"]),
        is_live=bool(raw["is_live"]),
        format_description=str(raw["format_description"]),
        video_id=str(raw["video_id"]),
        # Optional: sites that do not report a live status (and test
        # fakes written before ticket #11) simply leave it empty.
        live_status=str(raw.get("live_status") or ""),
    )


# ---------------------------------------------------------------------------
# Plan + render
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """What the tool intends to do with one Item."""

    url: str
    kind: ItemKind
    profile: Profile
    format_description: str
    target_path: Path
    wait_for_stream: bool = False
    # How many Items the link's playlist holds; 0 for a plain video
    # (ticket #15).
    playlist_count: int = 0

    def render(self) -> str:
        """Return a human-readable block describing this Plan."""
        wait_text = (
            "would wait for the stream to start"
            if self.wait_for_stream
            else "would not wait"
        )
        playlist_text = (
            f"  Playlist : {self.playlist_count} Items\n" if self.playlist_count else ""
        )
        return (
            f"Plan for: {self.url}\n"
            f"  Item kind : {self.kind.name}\n"
            f"  Profile   : {self.profile.name}\n"
            f"  Format    : {self.format_description}\n"
            f"{playlist_text}"
            f"  Target    : {self.target_path}\n"
            f"  Stream    : {wait_text}\n"
        )

    def to_json(self) -> str:
        """Return a JSON-serialisable rendering of the Plan."""
        payload = {
            "url": self.url,
            "kind": self.kind.name,
            "profile": self.profile.name,
            "format_description": self.format_description,
            "target_path": str(self.target_path),
            "wait_for_stream": self.wait_for_stream,
            # Present even for a plain video (0), so both renderings of the
            # same Plan report the same count (ticket #25).
            "playlist_count": self.playlist_count,
        }
        return json.dumps(payload, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def resolve_plan(
    url: str,
    profile: Profile,
    output_root: str | Path,
    resolver: MetadataResolver,
    wait_for_stream: bool = False,
) -> Plan:
    """Resolve ``url`` into a Plan, but do not download.

    Performs the metadata request the real run would perform, fetches no
    media. The resolver is injected so tests never reach the network.
    """
    safe_url = _validate_url(url)
    raw = resolver(safe_url)
    metadata = _to_metadata(raw)

    if metadata.is_live:
        kind: ItemKind = CaptureKind()
    elif wait_for_stream and metadata.live_status == _UPCOMING:
        # A scheduled stream: not live yet, but the platform says it
        # will be. It is still a Capture -- the Queue waits for it to
        # go live and then records from the beginning (ticket #11).
        # Without --wait-for-stream nothing changes: it stays a
        # Download, exactly as before this ticket.
        kind = CaptureKind(is_live=False)
    else:
        kind = DownloadKind()

    # Capture container is always MKV; Download container comes from the
    # profile (PhoneSafe -> MP4). We override the file extension to match.
    if isinstance(kind, CaptureKind):
        ext = "mkv"
    else:
        ext = profile.container or metadata.ext

    target_path = resolve_target(
        output_root=output_root,
        uploader=metadata.uploader,
        title=metadata.title,
        video_id=metadata.video_id,
        ext=ext,
    )
    if metadata.playlist_count:
        # A Playlist's target is its output folder (ticket #23): the
        # executor puts one sanitised file per entry inside it. The
        # folder is the same name a single video of this link would
        # take, minus the extension.
        target_path = target_path.with_suffix("")

    return Plan(
        url=safe_url,
        kind=kind,
        profile=profile,
        format_description=metadata.format_description,
        target_path=target_path,
        wait_for_stream=wait_for_stream and isinstance(kind, CaptureKind),
        playlist_count=metadata.playlist_count,
    )


def render_summary(plans: Sequence[Plan], rejected: int = 0, failed: int = 0) -> str:
    """Render the closing line for ``--dry-run``.

    States explicitly that no Item was downloaded or written, then the count
    of Items that were successfully planned. ``rejected`` counts links refused
    during validation; ``failed`` counts links that resolved but still produced
    no Plan -- a refusal, a missing Prerequisite, or an unexpected failure
    (ticket #34). A preview where nothing was planned must not read like a
    clean preview (ticket #27).
    """
    parts = ["Nothing was downloaded or written."]
    count = len(plans)
    if count:
        item_word = "Item" if count == 1 else "Items"
        parts.append(f"{count} {item_word} planned.")
    if rejected:
        link_word = "link" if rejected == 1 else "links"
        parts.append(f"{rejected} {link_word} rejected.")
    if failed:
        link_word = "link" if failed == 1 else "links"
        parts.append(f"{failed} {link_word} failed.")
    return " ".join(parts) + "\n"
