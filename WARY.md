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
**Resolved by #22**: the 10 drifted files were reformatted and
`ruff format --check .` now runs in CI before the test suite.

## From #16 (Settings)

No reset/`--clear` for un-saving (the settings file is deliberately
hand-editable); `--wait-for-stream` and `--dry-run` are not
persistable (they are run modes, not preferences). Risk noted: the
settings file location inside Termux's private home is standard but
**untested on a real device** — same applies to captures landing on
`/sdcard/Movies/vicxiun`.

## From #11 (Wait for a scheduled stream)

- A *scheduled* stream reports `is_live=False` with
  `live_status="is_upcoming"`; that combination is what makes
  `--wait-for-stream` resolve it as `Capture(is_live=False)` instead of
  a Download. Without the flag nothing changed (still a Download).
- The poll loop lives in `vicxiun.queue._wait_for_stream_start` and the
  Queue owns the wait: a waiting Item blocks the rest of the Queue by
  sequence, not by any lock. Tests fake sleep at
  `monkeypatch.setattr("vicxiun.queue.time.sleep", ...)` — never
  real-wait in tests.
- Deliberate rule: while waiting, a check that *fails* (network hiccup)
  keeps waiting, but the first re-resolve that is no longer an
  upcoming Capture ends the wait — a live Capture is recorded from the
  beginning, and a Download (stream ended before any check saw it
  live) fetches the platform's archive rather than waiting forever.
- Scheduled-stream recognition assumes yt-dlp's `live_status`
  vocabulary. Other extractors that do not report `live_status` will
  not resolve as a waiting Capture — if a platform needs support, the
  check is in `resolve_plan` (one `elif`). Verified by hand only via
  the new checklist section ("The stream that has not started yet")
  on a real scheduled stream.
- `--wait-interval` (default 60, rejected if < 1 via parser.error →
  exit 2) is **not persistable**, same reasoning as `--wait-for-stream`
  in the #16 note.

## From #10

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
  **Resolved by #14**: skip-existing landed, so a re-run skips an Item
  whose file is already present — reaching that corner now needs
  `--no-skip`.
- Cleanup on Windows is per-file best-effort (`except OSError`) —
  yt-dlp can still hold an open `.part` handle when the interrupt
  arrives.

## From #14 (Link file + skip-existing)

- The skip record is `<output_root>/.vicxiun-done.txt`, one URL per
  line, exact-string match against the link as typed (ADR-0010). It is
  NOT a download history: no timestamps, rewritten in place after each
  finished Item, failures never recorded.
- Skip order matters: the record check runs in `run_queue` before any
  resolve (a re-run of a completed Job does zero network), and the
  file-presence check runs in `run_item` right after `resolve_plan`,
  BEFORE the stop-instruction print and the wait-for-stream loop — so
  a Capture whose MKV already exists is skipped without waiting.
- `--dry-run` ignores skipping entirely and never reads or writes the
  record — the spec's no-write guarantee is untouched.
- Tests reuse one metadata dict across links at your peril: equal
  title/uploader/id means one target path, and the second link is now
  *skipped* by design. Old suite needed distinct video_ids. Any new
  multi-link test must give each link its own id.
- Counts line gains a middle segment only when something was skipped:
  "0 ok, 2 skipped, 0 failed." — "N ok, M failed." shape is unchanged
  otherwise, so older assertions still hold.
- For #15 (large-playlist warn): decide whether skipped/recorded Items
  count toward the 50-Item threshold — they were asked for either way.
  **Resolved by #15**: the threshold counts what the playlist itself
  reports (every Item the link stands for); a record-skip happens
  before any resolve, so a recorded playlist never asks.

## From #15 (Warn before a very large playlist)

- A playlist is one link whose resolve reports `_type: "playlist"` and
  `playlist_count`. The engine resolver now sets
  `extract_flat: "in_playlist"`, so a huge channel resolves fast
  (count only, no per-entry fetch); direct video links are unaffected.
- The confirmation is asked in `run_item` after the resolve, before
  anything of the playlist is fetched: the Queue prints the count
  statement (stderr) and calls the injected `confirm` callback. The
  CLI seam is `cli.set_confirm` (fakes: `install_confirm`); the
  built-in prompt reads stdin, and only `y`/`yes` confirms — empty,
  other answers and EOF (piped scripts) all decline.
- `--playlist-limit N` (default 50): above N Items asks; `0` turns it
  off; negative is a `parser.error` (exit 2). Not persistable in
  settings — same reasoning as `--wait-interval` in the #16 note.
- Declining is not a failure: nothing downloads, the declined link is
  NOT written to the skip record (it is not done — the next run asks
  again), the Queue stops, Items after it are named as not run
  ("Declined: N Item(s) were not run."), and the exit is 0 when
  nothing else failed.
- **Known un-ticketed limit**: the executor still passes the Plan's
  single target path as yt-dlp's `outtmpl`, so a playlist download
  writes every entry to the SAME file — each entry overwrites the
  previous one and only the last entry survives. The confirmation
  guards the disk; per-entry naming (and re-thinking the path-cleaning
  guarantees for yt-dlp-chosen names) needs its own ticket if
  playlists become a real use case. Do not "fix" this by switching to
  yt-dlp's own output templates: that bypasses vicxiun's hostile-title
  sanitisation (security surface, spec rule 43).
  **Ticket #23 created for this** while working on #18.

## From #18 (README + Android docs)

- `--dry-run` was broken against ANY real link on `main`: yt-dlp's raw
  info dict names the video id `id` (not `video_id`) and carries no
  `format_description`, while `plan._to_metadata` demands both. Fixed
  by `normalise_resolver_result` at the engine resolver seam (injected
  test fakes still pass through unchanged); three unit tests pin it in
  `tests/test_engine.py`. Caveat: the mapped `format_description` is
  built from `resolution` + `ext` and is a dry-run DISPLAY string only
  -- do not build decisions on it.
- Every command in the README was executed against the real CLI (real
  network where the command needs it): `--help`, `--version`,
  `--dry-run` (video + `--wait-for-stream` + `--from-file`),
  `--playlist-limit` real-run decline path,
  `vicxiun settings [key [value]]`, `uv run pytest` (285 passed, 1
  skipped). NOT yet verified: a real scheduled-stream wait, the PyPI
  install commands (#20), anything on a real device (#21) --
  docs/android.md says so explicitly.
- `docs/android.md` is the new Android page; README links to it and
  lists known limitations (incl. the #23 playlist single-file limit
  and the iOS refusal). If #21 or #20 change the story, update both
  pages.
- Ticket #23 (playlist one-file-per-entry) was created while working
  on #18; the WARY entry in "From #15" records it.

## From #12 (Disk-space protection)

- The running-floor stop is a `KeyboardInterrupt` raised from a
  progress hook (`vicxiun.diskspace.disk_watch`), so the shutdown
  path, the summary wording ("Interrupted: N Item(s) were not run.")
  and exit 130 are the deliberate-stop contract reused verbatim —
  what the ticket meant by "clean shutdown, not a kill". Low space
  reports exit 130 like an interrupt; do not "fix" the summary
  wording to say "disk" unless the Queue learns a second stop
  reason.
- The check rides yt-dlp's progress hooks, throttled by
  `time.monotonic` to one measure per `CHECK_INTERVAL_SECONDS`.
  Tests fake the clock at
  `monkeypatch.setattr("vicxiun.diskspace.time.monotonic", ...)` —
  the mirror of the `queue.time.sleep` pattern — and fake space at
  `monkeypatch.setattr(vicxiun.diskspace, "free_bytes", ...)` (plain
  value, `_free_space` style, or a sequence via
  `_free_space_sequence` for "room at pre-flight, short mid-run").
  Known limit: a transfer whose hooks never fire is never checked.
- The merge/postprocess phase is NOT covered by the running check
  (hooks stop at download end). A merge that fills the disk
  surfaces as a DownloadError → UserError, not a clean stop; the
  pre-flight floor's headroom is the guard there.
- `free_bytes` walks up to the nearest existing ancestor of the
  target (a first run's output folders do not exist yet). An
  unmeasurable volume (`OSError`) skips the check instead of failing
  the run — a guard must not break the run it guards, and printing
  it would spam one line per interval.
- On a real **live** fragment download, yt-dlp's fragment loop catches
  the hook's KeyboardInterrupt for live streams and converts it to a
  graceful finish (it finalises the recording instead of propagating;
  `downloader/fragment.py`). So `SpaceWatch` carries a `tripped`
  flag: `run_item` re-raises after the executor returns and ends the
  Item on the interrupt path all the same. Consequence worth knowing
  for the #10 real-device work: a real Ctrl+C landing inside the
  fragment download of a live Capture is ALSO swallowed this way by
  yt-dlp itself — the file is finalised and kept, but the outcome can
  be "ok" instead of 130 depending on where the signal lands. That is
  existing yt-dlp behaviour, not new in #12; verifying the deliberate
  stop with a real stream (checklist steps 3–5) should record whether
  exit was 130 or 0.
- The manual checklist gained "The disk that runs short" (steps
  16–19). As the #10 note says for #11: run it on a real device
  before claiming #12 fully verified — the automated tests prove
  the Queue paths, not yt-dlp's real hook cadence.
- README gained "Running out of disk space"; GLOSSARY gained
  Pre-flight floor and Running floor.

## From #22 (Format gate)

- `uv run ruff format --check .` now runs in CI on every push and PR,
  before the test suite. A push with drifted files fails at the format
  step, not at pytest. Run `uv run ruff format .` before committing;
  the fixed point is `56 files already formatted`.
- The drift commit is format-only (10 files; `plan.py`, `engine.py`
  shrank the most): long type aliases and string arguments that ruff
  had wrapped differently, plus a missing final newline in
  `engine.py`. No behaviour change; the full suite passed unchanged.

## From #17 (Security audit)

- The spec's rule table never numbered rules 4 and 8 anywhere; the audit named
  them: rule 4 = credentials never requested or stored (story 44), rule 8 =
  hostile-title containment (story 43). The enumeration lives in
  `docs/security-audit.md`; run it with `uv run pytest tests/test_security.py`.
- `test_rule_10` reads `docs/security-audit.md` and fails if the doc drifts
  from the ten rule tests: renaming a rule test means editing the doc's table
  in the same commit.
- Whole-package source scans guard rules 2 and 4 and the shell regression:
  a new module that mentions `winreg`/`cookiesfrombrowser`/`shell=True`
  even in a comment will trip `test_rule_2`/`test_rule_4`/the surface test.
  engine.py's existing "cookie is required" comment deliberately avoids the
  scanned strings.
- `test_rule_3` monkeypatches `subprocess.run`/`subprocess.Popen` to
  explode and `shutil.which` to `None`; monkeypatch undoes it, no ordering
  coupling with other tests.
