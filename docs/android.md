# Installing and running vicxiun on Android

Android support means [Termux](https://termux.dev): `vicxiun` is installed
and run inside that terminal environment as an ordinary command-line
program, and its finished files are placed in the phone's shared storage so
the gallery and file manager can see them
([ADR-0006](adr/0006-termux-is-the-android-path.md)). A native Android app
is deliberately out of scope for now.

Use the F-Droid or GitHub build of Termux, not the Google Play build: the
Play build is stale and its Python and pip cannot run this project.
Terms of what goes where: the tool itself lives in Termux's private home;
only the finished videos land on shared storage.

## Install

Inside Termux:

```console
pkg update
pkg install python ffmpeg nodejs
pip install vicxiun
```

`ffmpeg` is needed to mux audio and video into one playable file, `nodejs`
to run the JavaScript challenge solver that yt-dlp relies on. `pip` is the
only install path on Android because `uv` has no Android build — not a
preference, the only option ([ADR-0008](adr/0008-uv-on-pc-pip-on-android.md)).

Until the package is published to PyPI, install from a source checkout
instead:

```console
git clone https://github.com/cerc-xiun/vicxiun
cd vicxiun
pip install .
```

## Shared storage

`vicxiun` writes captures and downloads to `/sdcard/Movies/vicxiun` by
default, so the gallery can see them. Android only lets Termux touch
`/sdcard` after you grant it storage access once:

```console
termux-setup-storage
```

A dialog asks you to allow access; accept it. Without it, runs that try to
write to `/sdcard` fail on the first file. Change where files land with
`vicxiun settings folder <path>`.

## Keep Android from killing a long capture

Android aggressively stops background work, and a live capture is background
work that may run for hours. Two steps make it survivable:

1. Turn off battery optimisation for Termux: **Settings → Apps → Termux →
   Battery → Unrestricted.** On some phones it is under a separate
   "Battery optimisation" screen or app list; find Termux, then choose not
   to optimise.
2. Start a wake lock before a capture, so the CPU is kept running while the
   screen is off:

   ```console
   termux-wake-lock
   ```

   The lock is held until you release it with `termux-wake-unlock` or the
   session ends.

Even then, Android may interrupt a long capture without warning — Android's
process killer does not ask. This is a property of the platform, warned
about rather than hidden ([ADR-0006](adr/0006-termux-is-the-android-path.md)).
The tool is built for that: a Capture records to MKV in fragments, so an
interrupted capture still leaves a file that plays, up to the second the
interruption arrived. If a capture is interrupted that way, re-open the file
it named and check it plays; a step-by-step checklist is in
[docs/manual-capture-checklist.md](manual-capture-checklist.md).

## Running it

Exactly as on a PC — `vicxiun --help` works the same way. The most common
invocations:

```console
vicxiun https://example.com/the-video            # download one video
vicxiun --dry-run https://example.com/the-video  # preview, no files written
vicxiun --profile best ...                       # highest quality instead of phone-safe
vicxiun settings                                 # show or change saved settings
```

If you background the Termux session while a capture runs, Android may
suspend it despite the wake lock. Keep the capture in the foreground for the
duration of a long one. Nothing on this page has been verified on a real
device yet — that is tracked in the issue tracker
([#21](https://github.com/cerc-xiun/vicxiun/issues/21)).
