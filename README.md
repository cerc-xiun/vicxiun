# vicxiun

Download finished videos and capture live streams, from your terminal.

`vicxiun` is a command-line tool — and, later, an importable library — built on
[yt-dlp](https://github.com/yt-dlp/yt-dlp). Its aim is that you paste a link and
get a file that plays on any phone, without having to learn about format
selectors, codecs or containers. It installs nothing on your machine and never
fetches or runs code from the internet at runtime.

DRM-protected and paywalled content is refused, permanently. That is a feature
of the design, not a limitation.

## Status

The package installs, the `vicxiun` command exists, and `--dry-run` resolves
links into a printable Plan without downloading anything. Downloads, live
captures, waiting for a scheduled stream, the sequential Queue, remembered
settings, supplying links from a file and skipping what you already have all
work; what is left of Milestone 1 is tracked in the
[issue tracker](https://github.com/cerc-xiun/vicxiun/issues).

## Requirements

- Python 3.10 or newer.
- [uv](https://docs.astral.sh/uv/) to work on the project, or to install the
  tool on a PC. On Android, `pip` inside Termux is the install path.

## Working on it

```console
uv sync                              # create .venv and install everything declared
uv run vicxiun --version
uv run vicxiun --help
uv run vicxiun --dry-run <link>      # preview a Plan without downloading
uv run pytest                        # the test suite
```

`uv sync` is the only setup step. It creates `.venv/`, installs `vicxiun` into
it in editable mode, and installs the declared dependencies (`yt-dlp` and
`yt-dlp-ejs`, plus `pytest` for development) at exactly the versions recorded
in the committed `uv.lock`. Re-running it is cheap and changes nothing when
nothing changed.

`vicxiun --version` and `vicxiun --help` work as soon as `uv sync` has run,
because `uv` puts the environment's scripts on `PATH` for `uv run`. `--dry-run`
runs the metadata request the real run would run, prints a Plan and stops;
it creates no files anywhere.

## Waiting for a scheduled stream

Pass `--wait-for-stream` and start the tool before a stream begins: it checks
the link every `--wait-interval SECONDS` (the default is 60), says plainly that
it is waiting, and starts recording from the beginning the moment the stream
goes live. With `--dry-run` it reports that it would wait and returns
immediately.

A waiting Item **blocks the Queue**: the Queue handles one Item at a time, so
the links after it will not start until the stream does. If you need other
work to proceed, run the waiting Capture in a separate terminal.

## A list of links, and skipping what you already have

Hand the tool a prepared list in one go:

```console
vicxiun --from-file links.txt
```

The file holds one link per line, handled in order. Blank lines and lines
starting with `#` are ignored, so the list can carry notes. Links typed on the
command line are handled first, then the file's lines.

By default an Item you already have is skipped, so re-running the same command
is safe and downloads nothing twice. Two things count as "already have it":

- the Item's file is already in the output folder, or
- the Item was finished by an earlier run: finished Items are noted in
  `.vicxiun-done.txt`, kept beside the output.

Pass `--no-skip` to handle every link again regardless.

## Very large playlists

A link that turns out to be a playlist — a whole channel, say — is not
downloaded unnoticed. A playlist with more than 50 Items asks first, naming how
many Items were found; declining exits having downloaded nothing. The limit is
yours to set:

```console
vicxiun --playlist-limit 200 https://example.com/a-big-mix
```

`--playlist-limit 0` turns the confirmation off. At or below the limit a
playlist simply proceeds.

A playlist's videos land in one folder of their own, named for the
playlist — `Output/Uploader/Playlist title [playlist id]/` — with one
file per video inside, named after that video. The same naming and
safety rules apply to those files as to any other download. If the run
stops part-way, re-running the same command skips the videos already on
disk and fetches only the rest.

## Running out of disk space

Free space is watched at two floors, so a full disk cannot ruin a recording.
Before a Capture starts, the output volume must have at least 2 GB free; below
that the Capture does not start, and the message says what is required and
what is available. While anything is being written, free space is checked
every few seconds; below 500 MB the transfer stops the same way a deliberate
stop does — a Capture leaves the recording made so far, playable, and a
Download is cleared of partial files so a re-run starts clean. Both messages
name the floor, the space that was left, and what to do next. A Download is
not subject to the before-Capture floor. The floors are constants in
`vicxiun/diskspace.py` for easy changing.

## Tests

```console
uv run pytest
```

That single command runs the whole suite. The tests exercise the installed
command line — arguments in, output and exit codes out — and no test reaches
the network. CI runs the same command on Windows and Linux, on every push and
every pull request.

The security rules are audited by one test per rule, ten in total, in
`tests/test_security.py` — the enumeration and every covering test are listed
in [docs/security-audit.md](docs/security-audit.md), and the audit runs on its
own with `uv run pytest tests/test_security.py`.

## Installing it

The package is not on PyPI yet (that is tracked in the issue tracker), so
`uv tool install` and `pip install` will fail until it is.

Once the package is published to PyPI:

```console
uv tool install vicxiun     # PC
pip install vicxiun         # Android, inside Termux
```

Two different commands, and not a preference: `uv` has no Android build,
so `pip` inside Termux is the only option there
([ADR-0008](docs/adr/0008-uv-on-pc-pip-on-android.md)). The full Android
walkthrough — Termux, shared storage, wake locks and battery optimisation —
is in [docs/android.md](docs/android.md).

## Main commands

```console
vicxiun <link> ...                          # download / capture, in order
vicxiun --dry-run <link> ...                # print the Plan, write nothing
vicxiun --profile {audio,best,capture,phone-safe} <link>
vicxiun --folder <path> <link>              # override where files land
vicxiun --wait-for-stream <link>            # record a scheduled stream from its start
vicxiun --from-file links.txt               # one link per line; blank lines / '#' ignored
vicxiun --no-skip <link> ...                # re-run even if the file or skip record exists
vicxiun --playlist-limit 0 <link>           # never ask before a huge playlist
vicxiun settings                            # show every saved setting
vicxiun settings profile best               # save a value
```

`vicxiun --help` shows everything, including the defaults. Choices made
once are remembered under `vicxiun settings`; a flag on a run overrides a
saved value and is never saved.

## Why captures are MKV, not MP4

A Capture (a live stream) records to MKV; a Download writes MP4. A live
stream cannot be resumed, and an interrupted MP4 frequently will not open —
while MKV survives being cut off mid-way, so a capture stopped deliberately
or by Android still leaves a playable file
([ADR-0007](docs/adr/0007-captures-record-to-mkv.md)). Remuxing a finished
capture to MP4 is set aside for a later version.

## Known limitations

Stated plainly, so nobody finds them by surprise:

- **iOS is not supported.** Getting a real terminal and a real Python on
  iOS without a Mac and a developer account is not currently possible, so
  there is no honest install path to document. Android is supported
  because Termux provides exactly that
  ([ADR-0006](docs/adr/0006-termux-is-the-android-path.md)).
- **DRM and paywalled content is refused, permanently.** A feature, not a
  gap — see the statement at the top of this file.
- **A playlist that fails part-way keeps what finished.** A playlist
  downloads one file per video into its own folder; a failure or an
  interrupt stops the run there, and re-running the same command skips
  what is already on disk and fetches only the rest.
- **Windows cleanup after an interrupt is best-effort** — a `.part` file
  can remain if yt-dlp still holds it open; the next run finishes or skips
  the Item, so nothing is lost.
- **Android may kill a long capture** even with a wake lock and battery
  optimisation off; the MKV container is the safety net.
- **The Android instructions are unverified on a real device yet.** The
  settings file inside Termux's private home and captures landing on
  `/sdcard/Movies/vicxiun` are standard setups, but the real-device smoke
  test is tracked in the issue tracker.

## Licence

MIT. See [LICENSE](LICENSE).
