"""Tests for the ``vicxiun.paths`` module.

Filename sanitisation and target-path construction are the two most dangerous
security surfaces in the project. They are exercised as pure functions, with
exhaustive unit tests, because reaching every edge case through the CLI alone
would be tedious and because the spec demands that containment be asserted
*independently* of the sanitisation transform.

The CLI integration tests in ``test_cli.py`` add the through-the-command
coverage. The pure unit tests here are the exhaustive layer.
"""

from __future__ import annotations

import string

import pytest

from vicxiun.paths import (
    UNKNOWN_UPLOADER,
    PathError,
    resolve_target,
    sanitize_filename,
)

# ---------------------------------------------------------------------------
# Layer 1: sanitize_filename()
# ---------------------------------------------------------------------------


def test_sanitize_passes_through_a_plain_unicode_title() -> None:
    assert sanitize_filename("My Favourite Talk") == "My Favourite Talk"


def test_sanitize_strips_invalid_windows_characters() -> None:
    """Reserved on Windows: < > : " / \\ | ? * plus control characters."""
    for ch in '<>:"/\\|?*':
        assert sanitize_filename(f"a{ch}b") == "a b", ch


def test_sanitize_strips_windows_reserved_device_names() -> None:
    """CON, PRN, AUX, NUL, COM1..COM9, LPT1..LPT9 (case-insensitive)."""
    for name in ("CON", "PRN", "AUX", "NUL", "COM1", "LPT9"):
        assert sanitize_filename(name) != name
        assert sanitize_filename(name.lower()) != name.lower()


def test_sanitize_appends_a_discriminator_to_reserved_names() -> None:
    """Bare reserved names must not survive: even with a suffix stripped,
    a refresh would land on ``CON.txt``. We append ``_`` so collision-free."""
    assert sanitize_filename("CON").lower() != "con"


def test_sanitize_strips_trailing_dots_and_spaces() -> None:
    """Windows ignores trailing dots and spaces. Android shared storage
    is happier without them as well."""
    assert sanitize_filename("title.") == "title"
    assert sanitize_filename("title   ") == "title"
    assert sanitize_filename("title .") == "title"


def test_sanitize_collapses_runs_of_whitespace() -> None:
    assert sanitize_filename("a    b") == "a b"
    assert sanitize_filename("a\t\nb") == "a b"


def test_sanitize_returns_a_safe_fallback_for_empty_input() -> None:
    """An empty title, ``.``, ``..``, or one that strips down to nothing,
    yields a sensible default filename rather than an empty string."""
    for bad in ("", ".", "..", "   ", "...", "///"):
        out = sanitize_filename(bad)
        assert out != ""
        assert out not in {".", ".."}


def test_sanitize_truncates_an_overly_long_title() -> None:
    """Android shared storage caps at roughly 255 bytes per path segment,
    Windows at 255 characters per filename; 200 is a generous, defensive
    ceiling applied before the [id] suffix is added."""
    long = "x" * 500
    assert len(sanitize_filename(long)) <= 200


def test_sanitize_returns_the_same_value_for_the_same_input() -> None:
    """Sanitisation is a pure function: deterministic."""
    title = "A Talk (Re-upload) — extended cut"
    assert sanitize_filename(title) == sanitize_filename(title)


# Property-style: generated hostile inputs are sanitised without throwing.
def test_sanitize_is_total_over_unicode_letters_and_symbols() -> None:
    """Every printable codepoint survives sanitisation without error,
    and the result is safe to use as a filename segment."""
    sample_chars = (
        string.ascii_letters
        + string.digits
        + " "
        + "".join(chr(c) for c in range(0x100))
    )
    for ch in sample_chars:
        out = sanitize_filename(f"aa{ch}bb")
        assert out != ""
        # Never contains a Windows-illegal char after sanitisation.
        assert not any(c in out for c in '<>:"/\\|?*')


# ---------------------------------------------------------------------------
# Layer 2: resolve_target()
# ---------------------------------------------------------------------------


def test_resolve_target_places_video_under_uploader_folder(tmp_path) -> None:
    target = resolve_target(
        output_root=tmp_path,
        uploader="Some Channel",
        title="Best of 2024",
        video_id="abc123XYZ",
        ext="mp4",
    )
    assert target.name == "Best of 2024 [abc123XYZ].mp4"
    assert target.parent.name == "Some Channel"
    assert target.parent.parent == tmp_path.resolve()


def test_resolve_target_uses_unknown_folder_when_uploader_is_empty(tmp_path) -> None:
    target = resolve_target(
        output_root=tmp_path,
        uploader="",
        title="Best of 2024",
        video_id="abc123XYZ",
        ext="mp4",
    )
    assert target.parent.name == UNKNOWN_UPLOADER
    assert target.name == "Best of 2024 [abc123XYZ].mp4"


def test_resolve_target_uses_unknown_folder_when_uploader_whitespace(tmp_path) -> None:
    target = resolve_target(
        output_root=tmp_path,
        uploader="   ",
        title="title",
        video_id="id",
        ext="mp4",
    )
    assert UNKNOWN_UPLOADER in target.parts


def test_resolve_target_expands_user_in_output_root() -> None:
    """``~``-style paths expand so preview output is verifiable in CI."""
    import os

    home = os.path.expanduser("~")
    if home == "~":  # platform without a home dir; skip on those.
        pytest.skip("no home directory available on this platform")
    target = resolve_target(
        output_root="~/vicxiun",
        uploader="Chan",
        title="title",
        video_id="id1",
        ext="mp4",
    )
    assert target.parts[:1][0] != "~"
    assert "Chan" in target.parts


# Containment: every result must resolve inside output_root.
def test_resolve_target_is_contained_for_a_traversal_title(tmp_path) -> None:
    target = resolve_target(
        output_root=tmp_path,
        uploader="Chan",
        title="../../etc/passwd",
        video_id="id",
        ext="mp4",
    )
    resolved = target.resolve()
    assert resolved.is_relative_to(tmp_path.resolve())


def test_resolve_target_is_contained_for_backslash_traversal(tmp_path) -> None:
    target = resolve_target(
        output_root=tmp_path,
        uploader="Chan",
        title="..\\..\\Windows",
        video_id="id",
        ext="mp4",
    )
    assert target.resolve().is_relative_to(tmp_path.resolve())


def test_resolve_target_is_contained_for_absolute_path_title(tmp_path) -> None:
    target = resolve_target(
        output_root=tmp_path,
        uploader="Chan",
        title="/etc/passwd",
        video_id="id",
        ext="mp4",
    )
    assert target.resolve().is_relative_to(tmp_path.resolve())


def test_resolve_target_is_contained_for_dot_titles(tmp_path) -> None:
    for title in (".", ".."):
        target = resolve_target(
            output_root=tmp_path,
            uploader="Chan",
            title=title,
            video_id="id",
            ext="mp4",
        )
        assert target.resolve().is_relative_to(tmp_path.resolve())


def test_resolve_target_is_contained_for_empty_title(tmp_path) -> None:
    target = resolve_target(
        output_root=tmp_path,
        uploader="Chan",
        title="",
        video_id="id",
        ext="mp4",
    )
    assert target.resolve().is_relative_to(tmp_path.resolve())


# Containment property-style: containment does not depend on the sanitiser
# working correctly. The transform is asserted at the next layer.
@pytest.mark.parametrize(
    "hostile_title",
    [
        "../../etc/passwd",
        "..\\..\\Windows",
        "/etc/passwd",
        "C:\\Windows\\System32",
        ".",
        "..",
        "",
        "////",
        "\\\\\\\\",
        "a/b/../../../b",
    ],
)
def test_resolve_target_contains_every_hostile_input(
    tmp_path, hostile_title: str
) -> None:
    target = resolve_target(
        output_root=tmp_path,
        uploader="Chan",
        title=hostile_title,
        video_id="id",
        ext="mp4",
    )
    resolved = target.resolve()
    assert resolved.is_relative_to(tmp_path.resolve()), (
        f"escaped: output_root={tmp_path} resolved={resolved}"
    )


# Two same-title videos must not overwrite one another. The [id] suffix
# distinguishes them.
def test_resolve_target_two_same_titles_get_different_paths() -> None:
    a = resolve_target("/out", "Chan", "Same Title", "idAAA", "mp4")
    b = resolve_target("/out", "Chan", "Same Title", "idBBB", "mp4")
    assert a != b


def test_resolve_target_two_different_ids_resolve_distinctly(tmp_path) -> None:
    a = resolve_target(tmp_path, "Chan", "Title", "idAAA", "mp4")
    b = resolve_target(tmp_path, "Chan", "Title", "idBBB", "mp4")
    assert a.resolve() != b.resolve()


# Filename segment (last component) is always a safe, non-empty string and
# never bare reserved names, contains [id], has the chosen extension.
def test_resolve_target_filename_segment_is_safe(tmp_path) -> None:
    target = resolve_target(tmp_path, "Chan", "Con", "id", "mp4")
    name = target.name
    base = target.stem
    assert name.endswith(".mp4")
    assert "[id]" in name
    # The base (before extension) must not be a bare reserved device name.
    core = base.split(" [", 1)[0].strip().upper()
    assert core not in {"CON", "PRN", "AUX", "NUL"}
    for i in range(1, 10):
        assert core not in {f"COM{i}", f"LPT{i}"}


# Containment must NOT depend on what the sanitiser did. We assert it here
# with a separate, redundant mechanism: any component of the target path
# must, when joined to output_root, stay inside it.
def test_resolve_target_contains_path_independently_of_sanitisation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replace ``sanitize_filename`` with the identity function to prove
    containment is enforced independently."""
    import vicxiun.paths as paths_mod

    monkeypatch.setattr(paths_mod, "sanitize_filename", lambda s: s)
    target = paths_mod.resolve_target(
        output_root=tmp_path,
        uploader="Chan",
        title="../../etc/passwd",
        video_id="id",
        ext="mp4",
    )
    assert target.resolve().is_relative_to(tmp_path.resolve())


# Empty uploader after sanitisation (e.g. "   ") lands in _unknown.
def test_resolve_target_uses_unknown_when_uploader_sanitises_empty(
    tmp_path,
) -> None:
    target = resolve_target(tmp_path, "///", "title", "id", "mp4")
    assert UNKNOWN_UPLOADER in target.parts


# Errors: nothing raises for hostile input; the function never yields a path
# outside output_root. A separate error type is reserved for paths *outside*
# the layout entirely.
def test_path_error_is_a_distinct_type() -> None:
    err = PathError("nope")
    assert isinstance(err, Exception)
