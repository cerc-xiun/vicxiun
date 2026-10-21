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
captures, waiting for a scheduled stream, the sequential Queue and remembered
settings work; what is left of Milestone 1 is tracked in the
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

## Tests

```console
uv run pytest
```

That single command runs the whole suite. The tests exercise the installed
command line — arguments in, output and exit codes out — and no test reaches
the network. CI runs the same command on Windows and Linux, on every push and
every pull request.

## Installing it

Once the package is published to PyPI:

```console
uv tool install vicxiun     # PC
pip install vicxiun         # Android, inside Termux
```

## Licence

MIT. See [LICENSE](LICENSE).
