# Manual checklist: stopping and interruption on a real stream

The automated tests exercise the interrupt paths against a synthetic
stream (no network, a fake yt-dlp call at the engine seam). The parts
that only a real live stream can prove are listed here, because the
whole reason ticket #10 exists is that hours of recording must never be
wasted. Run this on a real stream -- a platform't own uninterrupted
test channel is fine -- before trusting a Capture on it.

Run once on a PC (`uv` install) and once on Android/Termux when
possible. yt-dlp's live behaviour differs by extractor; if any check
fails, record the platform, the yt-dlp version (`yt-dlp --version`)
and the site.

## The stop that is your choice

1. `vicxiun https://www.youtube.com/watch?v=<LIVE_ID>`
   on a stream that is already live.
2. Confirm, before recording visibly starts, that stderr says:
   `stop: press Ctrl+C once to end the Capture`. The line must appear
   before any progress lines.
3. Record for at least a minute, then press **Ctrl+C exactly once**.
4. Expected: the tool prints one result line and exits 130, with the
   kept path named in the message. Open the exact path printed
   (not a `.fNNN`-numbered fragment beside it -- if that is what got
   printed, that is a bug to record). It must play, including the
   earliest second the tool captured (`live_from_start`).
5. Open the file it pointed to: it plays, including the earliest
   second the tool captured (`live_from_start`).

## The stop that is not your choice

6. Start a Capture, then kill the terminal process from outside
   (close the terminal window, or `kill -9`, or pull the Termux
   notification). Nothing in vicxiun runs -- this checks the container
   guarantee alone (MKV + MPEG-TS, ADR-0007).
7. Find `<output>/<Uploader>/<Title> [<id>].mkv` (or its `.part`
   sibling if the rename never happened) and open it: it plays.

## The stream that never ends

8. Repeat steps 1–5 on a stream that has been live for hours; partway
   through, press Ctrl+C once. Same expectation: one interrupt, a
   file, an honest result line, exit 130.

## Interrupting a Download, not a Capture

9. `vicxiun <finished-video-link>` and press Ctrl+C mid-download.
10. Expected: exit 130, no `.part` / `.ytdl` / `.fNNN.*` files left
    beside the target, no final file at all (or none that fails to
    open). Re-running the link completes the download normally.

## The Queue around an interruption

11. Give three links -- one finished video, one live capture, one
    more finished video. Interrupt during the Capture.
12. Expected: the first video's file opens, the Capture is kept, the
    third link is reported as not run, exit 130.

## The stream that has not started yet

13. Find an upcoming (scheduled) live stream. A few minutes before it
    is due, run
    `vicxiun --wait-for-stream --wait-interval 15 <link>` so each
    check is visible.
14. Expected while it waits: stderr says the stream has not started
    and that it is checking every 15 seconds, with a line per check.
    It must be clear the tool is working rather than stuck.
15. When the stream goes live (or run it in a separate terminal to see
    that the rest of your work is blocked until then -- that is the
    documented behaviour), recording must begin at the very first
    second the platform offers (`live_from_start`) with the stop
    instruction shown before it. Stop and verify the file as in
    steps 3-5.

## Android/Termux notes

- The Settings file lives in Termux's private home; check that this
  does not affect where captures land (`vicxiun settings`).
- Android may kill a long background process unpredictably; if it
  does during a Capture, apply the steps in "The stop that is not
  your choice" afterwards and record the outcome honestly.
