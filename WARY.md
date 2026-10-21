# WARY — notes for the next agent sessions

Handed-down notes from earlier tickets, plus observations from ticket
#10. Read this before starting a ticket; add a short entry when you
leave something behind that the next session must know.

## From #13 (Queue)

On a mid-download interrupt, yt-dlp may leave a `.part` file for the
interrupted Item. **Handled now by #10**: `vicxiun.capture`
(cleanup in `remove_partial_download`, deletion gated on yt-dlp's
partial suffixes). Completed Items remain guaranteed intact — the
Download-interrupt cleanup only removes a final file that did not
exist when the Item began.

## From #7 (Profiles)

`ruff format --check` reported pre-existing drift on `main` in
`conftest.py`, `engine.py`, `plan.py`, `test_dry_run.py`,
`test_engine_execute.py`, `test_plan.py` — later confirmed as 9 files
(and `cli.py`, `test_settings.py`, among them) as of ticket #10. It
was deliberately left alone rather than bundled into a feature ticket.
**Status: STILL NOT FIXED.** CI has no format gate (`.github/workflows/ci.yml`
runs `uv run pytest` only), so CI green does not mean format-clean.
Worth its own one-line cleanup ticket: `uv run ruff format .` + test.
**Ticket #22 created for this** while working on #10.

## From #16 (Settings)

No reset/`--clear` for un-saving (the settings file is deliberately
hand-editable); `--wait-for-stream` and `--dry-run` are not
persistable (they are run modes, not preferences). Risk noted: the
settings file location inside Termux's private home is standard but
**untested on a real device** — same applies to captures landing on
`/sdcard/Movies/vicxiun`.

## From #10 (this ticket)

- `docs/manual-capture-checklist.md` needs to be run by hand on a real
  live stream (and ideally on an Android device). The automated tests
  only prove a synthetic stream; "an unclean interruption leaves an
  openable MKV" is a container-level guarantee that only a real
  yt-dlp run can confirm. Until then, do not claim #11/#12 fully
  verified.
- Timestamps were ruled out for "was this file written by this run?"
  — a filesystem write timestamp can trail `time.time()` by
  microseconds (measured: 212/300 anomalies on this machine), so the
  Download cleanup uses a plain pre-executor existence snapshot
  instead. Don't "simplify" it back to mtime comparisons.
- Known documented limit: an interrupted merge of a link whose file
  already exists can leave the old copy broken (ffmpeg truncates the
  final file before the interrupt can say anything). Accepted;
  re-running finishes it. If a `skip-existing` ticket lands later
  (#22 in the user stories), it makes this corner rarer still.
- Cleanup on Windows is per-file best-effort (`except OSError`) —
  yt-dlp can still hold an open `.part` handle when the interrupt
  arrives.
