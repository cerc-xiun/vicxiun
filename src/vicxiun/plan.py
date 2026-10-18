"""Planning layer: resolve a link into a concrete plan without downloading.

The Plan is the contract between "resolve" and "execute": the same code
path produces the Plan for both ``vicxiun`` and ``vicxiun --dry-run``. The
real run then executes it; the preview prints it and stops.

The plan is *not* a parallel implementation -- it sits exactly on the
execution path. The engine adapter is injected so tests never reach the
network. Two child kinds -- ``Download`` and ``Capture`` -- are decided by
inspecting the link (``is_live``).
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
    # Shell metacharacters that suggest argument injection.
    shell_metas = ("$", "`", "|", ";", "&", ">", "<", "\n", "\r", "\0")
    if any(m in url for m in shell_metas):
        raise InvalidLinkError("a link contains an unsafe character")
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

    The selector expressions are placeholders that pin the *shape* of the
    Profile for this milestone; ticket #7 (Profiles and the phone-safe
    default) owns the values.
    """

    name: str
    format_selector: str
    format_description: str
    container: str


_PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        Profile(
            name="phone-safe",
            format_selector=(
                "bestvideo[vcodec^=avc1][height<=?1080]"
                "+bestaudio[acodec^=mp4a]/best"
            ),
            format_description="MP4, H.264 video + AAC audio, up to 1080p",
            container="mp4",
        ),
        Profile(
            name="best",
            format_selector="bestvideo+bestaudio/best",
            format_description=(
                "bestvideo + bestaudio in the source container"
            ),
            container="mkv",
        ),
        Profile(
            name="audio",
            format_selector="bestaudio/best",
            format_description="best audio stream",
            container="mp3",
        ),
        Profile(
            name="capture",
            format_selector="best",
            format_description=(
                "bestvideo+bestaudio recorded as MKV until stopped"
            ),
            container="mkv",
        ),
    )
}


def profile_from_name(name: str) -> Profile:
    """Return the Profile named ``name``, or raise ``UserError``."""
    if name not in _PROFILES:
        raise UserError(
            f"unknown profile: {name!r}; "
            f"expected one of {sorted(_PROFILES)}",
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
    """The metadata the engine yields from a non-downloading extract."""

    title: str
    uploader: str
    ext: str
    is_live: bool
    format_description: str
    video_id: str


class MetadataResolver(Protocol):
    """Anything that resolves a URL to metadata without downloading media.

    The real implementation uses ``yt-dlp`` with ``download=False`` and
    the cache disabled. Tests substitute a fake.
    """

    def __call__(self, url: str) -> object: ...


def _to_metadata(raw: object) -> Metadata:
    """Coerce an engine dict (or test fake) into a ``Metadata``."""
    if not isinstance(raw, dict):
        raise MetadataError("engine returned a non-mapping value")
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

    def render(self) -> str:
        """Return a human-readable block describing this Plan."""
        wait_text = (
            "would wait for the stream to start"
            if self.wait_for_stream
            else "would not wait"
        )
        return (
            f"Plan for: {self.url}\n"
            f"  Item kind : {self.kind.name}\n"
            f"  Profile   : {self.profile.name}\n"
            f"  Format    : {self.format_description}\n"
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

    kind: ItemKind = CaptureKind() if metadata.is_live else DownloadKind()

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

    return Plan(
        url=safe_url,
        kind=kind,
        profile=profile,
        format_description=metadata.format_description,
        target_path=target_path,
        wait_for_stream=wait_for_stream and isinstance(kind, CaptureKind),
    )


def render_summary(plans: Sequence[Plan]) -> str:
    """Render the closing line for ``--dry-run``.

    States explicitly that no Item was downloaded or written, and prints
    the count of Items that were successfully planned. The wording is the
    same for zero-, one- and many-Item Jobs; only the count differs.
    """
    count = len(plans)
    if count == 0:
        return "Nothing was downloaded or written.\n"
    item_word = "Item" if count == 1 else "Items"
    return (
        f"Nothing was downloaded or written. "
        f"{count} {item_word} planned.\n"
    )
