# Installing vicxiun's prerequisites on macOS

vicxiun needs three programs on a macOS machine: **ffmpeg**, **Node.js**, and **yt-dlp**.
When you run `vicxiun` for the first time and any of the first two are missing, the
tool prints an install command tailored to your platform. On macOS that command uses
[Homebrew](https://brew.sh), which is the most common way to install Unix tools on
macOS. If you do not use Homebrew, this page lists alternatives.

## Recommended: Homebrew

Install Homebrew first (one-time), then the two prerequisites:

```bash
brew install ffmpeg node
```

yt-dlp is a Python package; install or update it with `pip`:

```bash
python3 -m pip install -U yt-dlp
```

## Alternative: MacPorts

If you maintain your macOS environment with [MacPorts](https://www.macports.org)
instead of Homebrew, the equivalent commands are:

```bash
sudo port install ffmpeg nodejs
```

yt-dlp is not packaged in MacPorts; install it with `pip` as above.

## Alternative: no package manager

If you want to avoid a package manager entirely, both programs ship standalone
binaries:

- **ffmpeg** — [evermeet.cx/ffmpeg/](https://evermeet.cx/ffmpeg/) (unofficial but
  widely used) or build from source.
- **Node.js** — download an LTS installer from
  [nodejs.org](https://nodejs.org/en/download).

Either way, make sure the resulting binaries are on your shell's `PATH` so
`vicxiun`'s prereq check can find them.