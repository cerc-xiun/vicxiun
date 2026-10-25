# Review follow-ups — 2026-10-25

Where this comes from: a full-project review of `main` at `7796131` (whole
history, 50 commits, initial commit `2efccd4`), run on Windows 11 with
Python 3.14.7. Read together with `WARY.md`; that file records why the
deliberate limits below exist.

## Where the project stands

| Evidence (run 2026-10-25) | Result |
| --- | --- |
| `uv run pytest -q -rs` | 327 passed, 1 skipped (`tests/test_settings.py:278`, "XDG-only location" — expected on Windows) |
| `uv run ruff format --check .` | 57 files already formatted |
| CI matrix (`.github/workflows/ci.yml:21,25`) | `windows-latest` and `ubuntu-latest`, Python 3.10 and 3.14 |
| Milestone 1 (#1) sub-issues | 18 of 20 closed |

Open and **not agent-workable**: #20 (publish to PyPI) and #21 (Android
smoke test on a real device). Both need credentials or hardware only the
maintainer has.

The code is complete for Milestone 1. The two user-visible defects found by hand
on 2026-10-25 (§2.3 link validation, §2.5 upcoming streams) are fixed on `main`,
as are the rest of §2, including §2.9 (#38). What is left: the two hand checks
behind #28 and #30, the real-hardware verification in §4, and the unpublished
package (#20).

### Open findings index

Keep this table in step with the sections: a defect is `open` until a commit
closes it, then it gets the commit hash. New hand-found defects go in §2 with
the next free number (`2.6`, `2.7`, …) — same shape as the rest: the exact
command and output that showed it, where in the source, the smallest fix, and
the test owed. Findings that turn out to be documented, deliberate behaviour
move to §7 instead of being deleted. Things the user asks for while testing
that are *his* preference rather than a system defect go in §8, marked as a
preference and left for the next session to decide on.

| # | Defect | Found | Status |
| --- | --- | --- | --- |
| 2.1 | `--dry-run` does not honour the exit-5 contract | review | fixed ac3c3a5 |
| 2.2 | `Plan.to_json()` drops `playlist_count` | review | fixed c079e2f |
| 2.3 | any link with two query parameters is rejected (`&`) | hand, terminal | fixed 983df00 |
| 2.4 | wholly failed `--dry-run` closes with a reassuring line | hand, terminal | fixed c6f1374 |
| 2.5 | `--wait-for-stream` crashes on a real upcoming stream | hand, terminal | code fixed daf6993; hand check owed |
| 2.6 | a dead link aborts the whole Queue with a traceback | hand, terminal | fixed bcd0406 |
| 2.7 | progress is printed twice (yt-dlp bar + hook line) | hand, terminal | code fixed d1c887c; hand check owed |
| 2.8 | an unknown flag is accepted when it abbreviates a real one | hand, terminal | fixed c826b76 |
| 2.9 | members-only refused with the wrong reason and exit code | hand, question | fixed c215b97 |
| 2.10 | `--dry-run` miscodes refused/prerequisite resolve failures | review, reproduced | fixed 2b939d6 |
| 3 | story 33 retryable/not is implied, never stated | review | decided #32; implementation #33 open |
| 8.1 | progress: one updating line instead of many | hand, preference | decision owed |
| 8.2 | clean links automatically? (recommendation: no — accept them) | hand, preference | decision owed |
| 8.3 | a line with two links silently becomes one (yt-dlp eats the tail) | hand, preference | decision owed |
| 8.4 | paste links one by one, then say done (recommend: `--from-stdin`) | hand, preference | decision owed |
| 8.5 | members-only / account access — **withdrawn by the user** | hand, decision | withdrawn |
| 8.6 | rate limiting has no flag (sleep options missing) | hand, doc check | decision owed |

---

## 1. Do this before the first publish (#20)

- **`LICENSE:3` still says `Copyright (c) 2026 cerc-xiun`.** The ticket
  requires the real copyright holder's name before the first publish.
  Nothing else in the repo blocks it.
- The README already says the package is not on PyPI yet, so once #20
  lands, update README "Installing it" in the same commit.

## 2. Code defects worth a small ticket

### 2.1 `--dry-run` breaks its own exit-code contract

- Where: `src/vicxiun/cli.py:383` — `except (InvalidLinkError, UserError)`.
- What: `main()` has no top-level handler (`cli.py:267-345`), so an
  unexpected exception during a preview prints a traceback and exits
  **1**. The contract in the same file (`cli.py:276`) promises **5**
  ("unexpected failure — anything else that bubbles up"), and the real run
  honours it because the Queue normalises every per-Item failure
  (`queue.py:202-212`).
- Smallest fix: catch `Exception` in `_run_dry_run`, print one honest
  line, record it as `UnexpectedFailure`, keep the Item-count logic
  (`cli.py:393-397`) — then a partly-planned Job still exits 1 and a
  wholly-failed one 5. Add one test at the CLI seam with a resolver that
  raises `RuntimeError`.

### 2.2 `Plan.to_json()` silently drops `playlist_count`

- Where: `src/vicxiun/plan.py:352-362`; the human rendering has it at
  `plan.py:339-341`.
- What: two renderings of the same Plan disagree. Nothing user-facing
  breaks today because no `--json` flag exists and the only caller is
  `tests/test_plan.py:552`.
- Smallest fix: add `"playlist_count": self.playlist_count` to the payload
  and extend that test. Do it now, while the field is fresh, so a future
  `--json` cannot ship the gap.

### 2.3 Any link with two query parameters is rejected — highest severity

- Found by hand on 2026-10-25, after the review, with a real YouTube link:
  `https://www.youtube.com/watch?v=hsLiJP2rqS8&list=RDGMEMP...&start_radio=1&rv=...`
  → `a link contains an unsafe character`, exit 2.
- Where: `src/vicxiun/plan.py:60` — `shell_metas` includes `"&"`.
- Impact: every playlist/mix link (`&list=`), every share link with a
  timestamp (`&t=`), every `&si=` / `&feature=` / `&index=` variant, and the
  Share button's `&pp=` tracking parameter (second confirmation, 2026-10-25,
  on a plain single-video link: `watch?v=b2tjnxX8IHU&pp=0gcJCSwMAYcqIYzv` →
  exit 2, `0 ok, 1 failed.`). Both `--dry-run` and the real run, since both
  go through `resolve_plan` → `_validate_url` (`plan.py:382`). That is user
  story 1 and story 19 broken for the commonest YouTube share forms.
- **Workaround for the rest of the manual pass, until this is fixed:** cut
  the link at the first `&`. The video ID alone is enough for every YouTube
  URL, and the single-parameter form passes. Playlist links
  (`…/playlist?list=…`) cannot be worked around this way — they must wait
  for the fix.
- **The fix is acceptance, not automatic cleaning** — a user should never
  have to tidy a URL, but stripping query parameters would silently change
  what was asked for. §8.2 records why.
- Why CI is green: every URL in `test_plan.py:93-100` and the hostile
  table at `test_plan.py:146-160` has a single query parameter, and
  nothing asserts that `&` is rejected.
- Fix: drop `"&"` from the tuple and say why in the comment. It is safe —
  vicxiun never invokes a shell (yt-dlp's Python API; the source-level
  no-shell guard in `tests/test_security.py`), so `&` is data, and the
  remaining metacharacters stay rejected. No audit row changes.
- Add the regression test: a multi-parameter URL resolves, and the
  resolver receives the URL unchanged.
- Also worth one README line: in CMD and PowerShell an unquoted `&` breaks
  the *shell's* parse before vicxiun runs, so such links must be quoted.

### 2.4 A wholly failed `--dry-run` closes with a reassuring line

- Evidence, real terminal, 2026-10-25, CMD:
  ```
  a link contains an unsafe character
  Nothing was downloaded or written.
  ```
- Where: `_run_dry_run` ends with `render_summary(plans)` (`cli.py:389-391`),
  but `render_summary` (`plan.py:430-441`) only ever sees the Plans that
  succeeded. A preview where every link was rejected reads like a clean
  preview. Exit 2 carries the truth for scripts; the human at the terminal
  does not get it (story 8, story 53).
- Smallest fix: give `render_summary` a rejected count (default 0) and
  append `"1 link rejected."` when non-zero. The existing
  `"Nothing was downloaded or written" in captured.out` assertions still
  hold; one new test for the wholly-rejected case.
- While in there: name the offending characters in the validation message,
  via `repr` of each match and never raw — a `\n` or `\0` must not be
  echoed into the user's terminal. "a link contains an unsafe character" is
  unactionable on its own, and after §2.3 lands it is the message the next
  person hits.

### 2.5 `--wait-for-stream` is broken on a real upcoming stream

- Evidence, real terminal, 2026-10-25, PowerShell:
  `uv run vicxiun --dry-run --wait-for-stream "https://www.youtube.com/live/NcnmGiJSpbA?si=..."`
  → yt-dlp `DownloadError: [youtube] ...: This live event will begin in 27 hours.`
  followed by a full traceback, exit 1. Nothing was planned.
- Cause 1 — the resolver: `_default_resolver` (`engine.py:161-171`) builds its
  options without `ignore_no_formats_error`, so YouTube's
  `raise_no_formats(..., expected=True)` is fatal and the info dict
  (title, uploader, id, `live_status="is_upcoming"`) is discarded before
  `resolve_plan` runs. The upcoming branch at `plan.py:388` is therefore
  unreachable against a real YouTube event — it has only ever been reached
  by fake resolvers. This is the exact item `WARY.md` "From #18" lists as
  never verified.
- Fix 1: `options["ignore_no_formats_error"] = True` in `_default_resolver`
  only — **not** in `_safe_default_options`, which the executor shares: there
  a link with no formats must still fail. Comment the scope: any no-formats
  link now resolves and fails at execute instead, which is the right trade
  for a previewer and for the wait loop. The installed yt-dlp supports it
  (`yt_dlp/extractor/common.py:1265-1267`).
- Cause 2 — the seam: `execute()` translates `DownloadError`
  (`engine.py:142-145`) but `resolve_metadata()` is a bare passthrough
  (`engine.py:127-128`), so any resolve-time yt-dlp error becomes a raw
  traceback instead of a named error. Fix 2: wrap it in the same
  `except DownloadError: _translate_download_error(exc)`.
- Cause 3: the dry-run catch and missing top-level handler are §2.1/§2.4 —
  they turn any remaining surprise into a traceback plus exit 1.
- Expected after the fix: `Item kind : Capture`,
  `Stream : would wait for the stream to start`, a `.mkv` target, and
  `Format : unknown` (accurate: an event that has not started has no
  formats). Then the real `--wait-for-stream` run becomes testable, which is
  the last Milestone-1 behaviour checkable on Windows alone.
- Tests owed: one engine-seam test injecting
  `DownloadError("... will begin in 27 hours")` from the resolver → named
  error, and one assertion that the resolver's options carry
  `ignore_no_formats_error` while the executor's do not. The planning side
  already exists (`test_plan.py:437-500` for `is_upcoming` → waiting
  Capture, `tests/test_wait_for_stream.py` for the Queue loop) — do not
  duplicate it.

### 2.6 A dead link aborts the whole Queue with a traceback

- Evidence, real terminal, 2026-10-25, PowerShell:
  `uv run vicxiun "<good link>" "<truncated link>" --folder D:\vicxiun-test`
  → the first Item downloads and prints `ok (...)`, then yt-dlp's
  `DownloadError: ... Incomplete YouTube ID ... looks truncated` escapes as a
  full traceback and the process exits 1. The summary (`1 ok, 1 failed.`)
  never prints.
- Where: `queue.run_item:397` wraps `resolve_plan` in
  `except (InvalidLinkError, UserError)`; a `DownloadError` from the resolver
  is neither, so it propagates through `run_queue` → `cli._run_real` →
  `main`, which has no handler (§2.1).
- Impact: user story 20 ("one failed Item must not stop the rest") and story
  21 (the summary) fail for the commonest failure there is — a dead or
  mistyped link. The Queue survives only failures it can name, and every
  resolve-time yt-dlp failure is unnamed. Same root cause as §2.5 cause 2;
  translating at `YtDlpEngine.resolve_metadata` closes this too. Belt and
  braces: `_classify_failure` (`queue.py:202-212`) already exists to
  normalise an unnamed exception, so `run_item` could use it for the resolve
  step as well.
- Severity: joint highest with §2.3 — this is the Queue's core promise.
- Nothing is lost: the finished Item's file is on disk and its `ok` line was
  printed. What dies is the rest of the Queue and the summary.
- Test owed: a Queue test whose resolver raises `DownloadError`, asserting
  the Item is recorded failed, the next Item still runs, and the exit code is
  1. `tests/test_execute.py:254` covers the execute path; the resolve path
  has no equivalent.
- Not this defect: the `--from-file` line with two glued URLs did **not**
  crash — yt-dlp matched the leading ID and reported `ok` (see §8.3). Do not
  fold the two together; §2.6 is only the unnamed-exception path.

### 2.7 Progress is printed twice

- Evidence: every line of that download is doubled —
  `[download]  34.5% of 2.90MiB at 11.78MiB/s ETA 00:00  downloading: 34.5% at 11.78MiB/s ETA 00:00`.
- Cause: yt-dlp's own progress bar is still on. `_build_executor_options`
  sets `quiet` (`engine.py:326-327`), but yt-dlp does not tie the bar to
  `quiet`: the printer is chosen in `yt_dlp/downloader/common.py:293-297`
  (`QuietMultilinePrinter` only when `noprogress`), while `progress_hooks`
  fire regardless (`yt_dlp/downloader/common.py:488-495`). So yt-dlp writes
  its bar on stdout and `default_progress_printer` writes its own line on
  stderr; the console shows both, and the hook's newline destroys the bar's
  in-place update.
- Fix: `options["noprogress"] = True` in `_build_executor_options`
  (`engine.py:325`), beside `quiet`. Hooks keep firing, so the disk-space
  guard and the progress printer are unaffected, and `_download_playlist`
  inherits it through the same function. This makes vicxiun the only writer
  of progress — the precondition for §8.1.
- Test owed: assert `noprogress` is set in the executor options and that
  `progress_hooks` are still wired.

### 2.8 An unknown flag is silently accepted when it abbreviates a real one

- Evidence, 2026-10-25: `vicxiun --playlist 1 "<link>"` — `--playlist` is not
  a flag — ran the Queue instead of exiting 2 with "unrecognized arguments".
  argparse's prefix matching turned it into `--playlist-limit 1`
  (`cli.py:211`), because `build_parser` (`cli.py:127-135`) never passes
  `allow_abbrev=False`.
- Impact: a typo is indistinguishable from a real flag (story 32/35: name the
  problem and exit non-zero). Worse, `--playlist` stops being unambiguous the
  day a second `--playlist-*` option lands: the same command would then fail
  with "ambiguous option" — meaning changes between releases for the same
  input.
- Fix: `allow_abbrev=False` in the `ArgumentParser` call, plus one test
  asserting `["--playlist", "1", LINK]` exits 2 and names the flag. Also
  worth deciding whether `--wait`, `--no-s` and `--dr` should keep working as
  shorthands — with the option off they stop, which is the honest answer.
- Severity: low. Nothing is damaged; it is a truthfulness/contract issue.

### 2.9 A members-only video is refused with the wrong reason and exit code

- Found 2026-10-25 while answering a scope question.
- yt-dlp's message for a members-only video is "Join this channel to get
  access to members-only content"; the classifier looks for `"members only"`
  with a space (`engine.py:43`), so it never matches. The error falls through
  to `UserError` → exit 2 with `could not download …`, instead of
  `RefusedContent` → exit 4 and the ADR-0003 refusal message.
- Impact: nothing is downloaded (correct), but the cause is reported wrongly,
  and story 48 ("told clearly that DRM and paywalled content is refused") is
  bypassed on the one path a user is most likely to hit.
- Fix: also match the hyphenated form (add `"members-only"`, or normalise
  hyphens before comparing). One line plus one case in
  `tests/test_engine_execute.py` beside the `premium` test (line 311).
- While in there: `requires purchase` and `subscription required` are
  guesses, not observed strings. Only `drm`, `sign in to confirm`, `premium`
  and `members-only` have a known source; the rest should be checked against
  real messages or dropped.
- Severity: low (misleading, not damaging). **Raised in importance by §8.5's
  withdrawal:** refusing members-only content is now the tool's final answer for
  that content, not a stopgap before a feature, so the refusal has to be honest
  — exit 4 and the ADR-0003 wording, not "could not download".
- Fixed c215b97: hyphens are normalised before matching, and the two invented
  phrases were removed.

### 2.10 `--dry-run` miscodes refused and prerequisite resolve failures

- Found by the code review of #24–#31 on 2026-10-25.
- #28 makes `resolve_metadata` translate a resolve-time `DownloadError`
  into `RefusedContent` (exit 4) or `PrerequisiteMissing` (exit 3). #24's
  catch-all in `_run_dry_run` records every non-`UserError` as
  `UnexpectedFailure`, so both previews print `unexpected (...)` and exit
  5; a real run exits 4 and 3. The dry-run contract says the codes are
  identical.
- Reproduced with a resolver raising each class: both exit 5, and the
  summary says `1 link rejected.`
- Fix: catch the named classes (or reuse the Queue's classifier) before
  the catch-all; derive a wholly failed Job's exit code with
  `exit_code_for`; keep partial 1, user 2, prerequisite 3, refused 4,
  unexpected 5. The summary must not call a refusal a rejection.
- Ticket: #34. The WARY handover entry for #24–#31 belongs in the same
  commit.

## 3. Spec story that is thin, not missing — **decided in #32, implementation in #33**

- **Story 33** ("I want to know when a failure is worth retrying and when
  it isn't"). There is no retryable/not-retryable concept anywhere:
  `errors.py` names causes only, the Queue labels each failure
  (`queue.py:184-199`: `interrupted`, `refused`, `missing prereq`,
  `user error`, `unexpected`), and the disk messages say "run again"
  (`diskspace.py:101,161`).
- **The decision (#32, closed):** five of the six causes already carry a retry
  verdict in their message. The exception is a transient network failure, which
  is labelled `user error` and is therefore indistinguishable from a dead link
  — the one case story 33 is actually about. So option 2 was chosen: a
  `TransientError(UserError)` with label `temporary` and a pinned sentence,
  decided only by what the user is *told*. No retry mechanism, no backoff, no
  concurrency — out of scope for Milestone 1.
- **Owed (#33, open):** the implementation — the subclass, the label, the
  sentence, the phrase list, the rule that a 4xx stays `user error`, and the
  guard that the exit code remains 2.

## 4. Verify by hand before trusting it (this is the real "done")

The items below have not run outside the injected fake seam. In priority
order:

1. **Playlist download (ticket #23).** The whole one-file-per-entry flow
   has only been exercised by `tests/test_engine_playlist.py` against the
   fake seam. Run `--dry-run <playlist>` (shows the folder) and one small
   real playlist (one file per video, sanitised names, folder named for
   the playlist).
2. **`docs/manual-capture-checklist.md`, steps 1-19, on this Windows PC.**
   Live Ctrl+C, external kill, MKV opens, disk-full stop, queue around an
   interrupt. Nothing else can prove these.
3. **Android (#21).** Termux from F-Droid, shared storage, a real capture
   with the screen off.
4. **Real scheduled-stream wait** (checklist steps 13-15). Recognising
   `live_status="is_upcoming"` is yt-dlp vocabulary only; untested against
   a real upcoming stream.

Every failure found goes back as a new ticket — that is #21's own
acceptance criterion.

### Run log on Windows — 2026-10-25

What the manual pass has actually proved so far, so nobody re-does it:

- **PASS — one real Download** (`watch?v=OiAf8k0y2rw`, opts: default
  profile, `--folder D:\vicxiun-test`). Resolved, printed the Item line and
  the absolute target, downloaded 10.66 MiB video + 2.90 MiB audio, ffmpeg
  merged them into one `.mp4`, printed `ok (...)` with the full path. The
  title carried `&`, an emoji, a comma and a colon-free colon-ish phrase and
  landed unchanged in the filename. **Still to confirm by ear: the file
  plays with sound.**
- **FAIL — the second Item of that same run** (a link with a truncated video
  ID) killed the process with a traceback and no summary (§2.6).
- **FAIL — every link carrying `&`** is rejected before any work (§2.3),
  including both links in the first attempt, so the Queue never started.
- **FAIL — `--dry-run --wait-for-stream`** on a real upcoming event crashes
  (§2.5).
- **Deferred on purpose, 2026-10-25 (the user's call, not an oversight):**
  - *Playlist (real)* — was blocked on "what am I meant to run"; the recipe
    below is now in the file.
  - *Capture checklist steps 1-19* — every step needs a live or an upcoming
    stream at the moment it runs. The user will run them as those situations
    arise rather than on demand.
  - *Disk-full (steps 16-19)* — needs a deliberately small volume; same
    reasoning, run it when a USB stick or spare volume is at hand.
  - *Android (#21)* — no device session yet.
  These are **unverified, not passed**. Nothing may claim Milestone 1 is
  verified until they run.

### Playlist check, concretely

1. Pick or create a playlist of 2-3 videos. Its URL is
   `https://www.youtube.com/playlist?list=PL…` — one query parameter, so it
   is **not** blocked by §2.3 and runs today. Do not use a
   `watch?v=…&list=…` link (§2.3) or a channel `UU…` playlist (can be
   thousands of entries).
2. Preview:
   `uv run vicxiun --dry-run "<playlist url>" --folder D:\vicxiun-playlist-test`
   Expect `Playlist : N Items` and a **folder** target ending
   `<Uploader>\<Playlist title> [PL…]` — no `.mp4` suffix. The folder
   existing is not required; the preview writes nothing.
3. Run it: same command without `--dry-run`. Expect one `ok` line per entry
   with its own path, N files inside that one folder, exit 0.
4. Re-run the same command unchanged: every entry is skipped by yt-dlp's own
   no-overwrite behaviour, exit 0, nothing downloaded again.
5. Force the confirmation seam: add `--playlist-limit 1`. Expect the count
   stated on stderr and a `[y/N]` ask. `n` → `Declined: N Item(s) were not
   run.`, nothing downloaded, exit 0. `y` → it proceeds.

## 5. Refactor candidates (non-blocking, one ticket at most)

- **Duplicated seam types:** `ResolverCallable` / `ExecutorCallable` are
  declared identically in `engine.py:86-87` and `queue.py:58-59`, and the
  same ten-parameter clump travels through `run_queue` → `run_item`
  (`queue.py:340-351`, `596-607`). A single run-config object would stop a
  new option touching three signatures. Judgement call: the codebase
  deliberately keeps modules thin.
- **Duplicated path tail:** `paths.entry_target` (`paths.py:148-181`) and
  `paths.resolve_target` (`paths.py:184-233`) repeat the same
  `_is_rooted` → `_safe_component` → extension → `.resolve()` →
  containment-assert sequence. That is security code (spec rule 43) kept
  in sync by hand — extracting the shared tail removes the divergence
  risk. This one is worth doing before the security surface grows.
- **Cannot-fire branch:** `prereqs.check` skips when
  `name not in probe_result.which` (`prereqs.py:224-227`), but `probe()`
  fills every known program (`prereqs.py:352`). Harmless as written;
  delete it or leave the comment that explains the intent.
- **`__all__` drift:** `engine.py:385-391` omits
  `normalise_resolver_result` and `default_progress_printer`, both
  imported by other modules. Cosmetic.

## 6. Housekeeping

- The working tree is dirty with two unrelated things: `skills-lock.json`
  (adds a `caveman` skill entry) and untracked `.opencode/`. Keep both out
  of a release commit.
- `WARY.md` "From #7" is stale in one place: it still says the format
  drift is "STILL NOT FIXED" at line 23 before resolving it at line 27.
  Cheap to tidy if someone is in there anyway.

## 7. Deliberate behaviour — do not "fix" these

Recorded so a later session does not undo a decision recorded in `WARY.md`:

- Windows `.part` cleanup is per-file best-effort (`except OSError`):
  yt-dlp may still hold the handle.
- Download cleanup uses an existence snapshot, never mtime comparisons
  (212/300 measured anomalies).
- Captures are MKV, Downloads MP4 (ADR-0007).
- yt-dlp's own output templates stay forbidden — they bypass the hostile
  title sanitiser (rule 43).
- Low disk exits 130 through the interrupt path, with the interrupt
  wording.
- Playlist re-runs are incremental via yt-dlp itself; do not add a second
  per-entry skip check.
- Staleness is a 0.25-year advisory on the reported version
  (`prereqs.py:41-49`), not a real update check; the update checker stays
  off (ADR-0004).
- `--dry-run` never reads or writes the skip record.

## 8. Raised during manual testing — preferences, not decisions

Nothing in this section is agreed. It records what the user said while
running the tool, so the next session can decide with the reasoning in
front of it — adopt, reject, or ticket.

### 8.1 Progress output: one updating line, not a stream of lines

- **Status: the user's own preference, explicitly not a system choice.**
  He asked for it on 2026-10-25 while testing; the next session decides
  whether it is worth doing, and how.
- **Read §2.7 first.** Part of what he saw is not a preference but a defect:
  every progress line was printed twice, once by yt-dlp and once by
  vicxiun. Silencing yt-dlp's bar is a precondition for any single-line
  design, not an optional extra.
- **What he sees now:** one new line per yt-dlp progress event
  (`engine.py:399-423`), flushed on each event. On a fast connection that
  is tens of lines a second; a live capture adds a
  `finished downloading a fragment` line for every fragment on top.
- **What he asked for:** a single line that updates in place.
- **Why it is cheap to change if adopted:** vicxiun should own progress
  output completely. It does not yet (§2.7 — yt-dlp's bar is still drawn),
  but `default_progress_printer` is the only intended writer, wired in at
  `queue.py:41,462`; once `noprogress` is set, changing that one function
  changes every run.
- **Constraints a next session must respect if it adopts this:**
  - Keep it on stderr. stdout staying clean is deliberate
    (`cli.py:421-423`).
  - stderr is not always a TTY: redirected to a file, piped in CI, and
    captured by pytest. In-place `\r` must degrade to periodic readable
    lines there, never one enormous line.
  - The Queue interleaves its own lines on the same stream (the stop
    instruction, per-Item results, skip notes, the summary). An in-place
    line has to be cleared before any other line prints and redrawn after,
    or the output is garbage at exactly the moments the user needs to read
    it.
  - The disk-space guard rides the same progress hooks
    (`diskspace.py` `SpaceWatch`); throttling the printer must not throttle
    the disk check.
  - Tests assert on progress text today; grep `tests/` before changing the
    wording and keep what is captured readable.
- If adopted it changes what every run looks like, so it earns an ADR of
  its own rather than a silent tweak.

### 8.2 Should vicxiun clean links instead of making the user tidy them?

- **Raised by the user, 2026-10-25:** "a user having to clean a URL before
  it works is bad — add an automatic remover." Asked in response to the
  workaround in §2.3.
- **The principle is agreed: nobody should have to tidy a URL to use this
  tool.** The mechanism is not. The answer to §2.3 is to *accept* the link
  as typed, not to rewrite it.
- Why removal is worse than acceptance:
  - `&list=`, `&index=`, `&t=`, `&si=` are not all noise. The first two
    decide **what** is fetched, the third **where** playback starts. A
    cleaner would silently answer a different question than the user asked.
  - it needs a per-site list of "ignorable" parameters, which rots with
    every platform change — a maintenance bill for a one-character bug.
  - the tool must be honest about what it will do (story 36). Rewriting the
    link quietly is the opposite of that.
- **The real question hiding behind the request** — worth a decision, not
  code: a `watch?v=…&list=…` link resolves today as the **playlist**,
  because that is what yt-dlp reports. Once §2.3 is fixed, pasting a video
  link copied from inside a playlist will fetch the whole playlist, asking
  first only when it exceeds `--playlist-limit`. If that surprises the user,
  the options are a `--no-playlist` flag or a single-video default. That is
  a decision for the next session; the confirmation guard already caps the
  damage in the meantime.
- No code changes are implied by this section until that decision is made.

### 8.3 A line holding two links must not silently become one

- **Raised by the user, 2026-10-25:** a `--from-file` list with a missing
  newline (`…6UbvBMgK4c0https://youtu.be/HgfjSSv-epM`) downloaded only the
  first of the two links, with no warning. Follow-up question: "if I am lazy
  and dump links into a file, do we not accommodate all of them?"
- **What actually happens — confirmed in yt-dlp's source, not guessed.**
  The YouTube pattern matches `(?P<id>[0-9A-Za-z_-]{11})`, then
  `(?(1).+)?` ("anything can follow the ID"), then end-of-string
  (`yt_dlp/extractor/youtube/_video.py:86-118`). A glued pair, and equally a
  space-separated pair, therefore resolve as the **leading ID** with the
  remainder swallowed. vicxiun prints `ok` for a line that named two videos.
  That is story 36 bent: the report is not a lie about the file, but it is
  silent about having ignored half of it.
- **One link per line is the documented format** (`cli.py:249-264`,
  `tests/test_from_file.py`), so the input is malformed — the problem is the
  silence, not the refusal.
- **Recommendation (two parts):**
  1. **Split each line on whitespace.** A valid `http(s)` URL cannot contain
     a literal space, so `line.split()` cannot corrupt a well-formed link.
     That gives the lazy dump behaviour people actually want
     (`URL1 URL2 URL3` on one line, or a file pasted without newlines).
  2. **Refuse a token that still holds more than one `http`.** Glued pairs
     have no separator to split on, so any split would be a guess. Name it
     as that line's user error (`line 3 holds more than one link`) and let
     the Queue continue, which needs §2.6 fixed first.
- **Do not split glued text silently.** A URL can legitimately contain
  `http` again inside a query string; a splitter would corrupt a link the
  user typed correctly, and the tool would be guessing at intent.
- Cheap: `read_links_file` is fifteen lines (`cli.py:249-264`); the change is
  `for token in entry.split():`, plus the `http` count and a message. The
  README's "one link per line" sentence needs the same commit.
- **Whose problem is it:** ours, at the boundary — not a yt-dlp bug.
  `_validate_url` (`plan.py:48-70`) is the place that decides what vicxiun
  accepts; it checks scheme, host and shell metacharacters but never "is
  this one link". Passing a malformed string downstream and letting a
  lenient matcher interpret it is what this project says it does not do
  (story 36).
- **Why not split at every `http://` boundary:** a single legitimate URL can
  contain another one — `https://www.google.com/url?q=https://youtu.be/X` is
  a redirect link, not two links. A splitter would break it into two tokens
  and lose the link the user pasted. Glued pairs and nested-URL links are
  indistinguishable without parsing semantics we do not have, which is why
  whitespace splitting plus an explicit refusal is the safe pair of moves.
- **The `https:` regex split (raised by the user) is that same guess, and its
  failure is silent.** It handles every glued pair correctly and also cuts
  any legitimate URL that carries another URL unencoded. Most share wrappers
  percent-encode the inner one (`%3A%2F%2F`), so the regex would often
  survive — "often" is the problem: when it does not, the user gets a
  truncated link, an error about a URL they never typed, and possibly a stray
  download of the inner link. Rejected for now. If real use later shows the
  refusal is what annoys people, revisit with the evidence; do not guess
  ahead of it.
- Something to consider with part 2: run the same "more than one `http`"
  check in `_validate_url` as well, so a *quoted* glued argument on the
  command line earns the same honest message instead of the silent
  first-ID download. It is the same one-line test, one layer earlier.
- One wrinkle to decide with part 1: today a note after a link on the same
  line (`URL  # why`) is swallowed into the URL; under whitespace splitting
  it becomes Items of its own. Either extend the comment rule ("a token
  starting with `#` ends the line") or state plainly that notes must be on
  their own line. The existing whole-line `#` rule and the "hash inside a
  link" test (`test_from_file.py:161`) both stay intact either way.
- Status: decision owed, but this one is closer to a defect than §8.1/§8.2 —
  the current behaviour reports success for something it half-ignored.

### 8.4 Pasting links one after another, then saying "done"

- **Raised by the user, 2026-10-25:** a mode where links are pasted into the
  terminal one at a time, held in memory, and downloaded once the user
  signals they are finished — instead of preparing a file.
- **The need is real**, especially on Termux: pasting is easier than
  authoring a file path on a phone keyboard (story 47), and it removes the
  newline discipline that §8.3 is about.
- **Recommendation: implement it as stdin input, not as an interactive
  prompt.** "Paste lines until a signal, then download them" *is* reading
  stdin to EOF, and the Queue already takes a list — nothing else needs to
  change.
  - `vicxiun --from-stdin` (or `-`) reads links from stdin until EOF
    (Ctrl+Z then Enter in CMD/PowerShell, Ctrl+D on Unix/Termux).
  - Optionally accept a sentinel line (`done`) as an alternative terminator,
    for the phone case where no EOF key is convenient. Keep the sentinel to
    stdin only, so a file containing the word "done" is never truncated.
  - Order: command-line links first, then stdin, matching `--from-file`
    (`cli.py:297-307`).
- **Why not make bare `vicxiun` read stdin:** a user who mistypes and leaves
  the link off would get a CLI that silently hangs waiting for input. An
  explicit flag can never block by accident.
- **Why not the prompt loop as first imagined:** it invents a new
  interaction model for one convenience — a prompt, a terminator word, Ctrl+C
  semantics *during collection* (exit 130 with nothing downloaded) as well as
  during download, Windows console paste quirks, and a test seam for
  interactive stdin. That is an ADR-sized change to the CLI contract for
  something EOF already expresses. The stdin form needs roughly ten lines in
  `cli.py` plus one test, is scriptable, and composes with the shell:
  `Get-Clipboard | vicxiun --from-stdin` is the whole point on Windows.
- Note the decoding detail if adopted: stdin on Windows defaults to the
  console code page, not UTF-8. Links are ASCII in practice (anything else
  should be percent-encoded), but a `utf-8-sig` read on a pipe is where a BOM
  surprise would come from — the file reader already handles that
  (`cli.py:257`).
- Status: decision owed. If adopted it changes how the CLI is invoked, so it
  belongs with a README line and, ideally, an ADR — but it is a
  feature request, not a defect.

### 8.5 Reaching content the user's own account can see (members-only) — **withdrawn 2026-10-25**

- **Raised by the user, then withdrawn by the user the same day.** Kept as the
  record of *why*, so nobody rediscovers this from scratch or reopens the
  feature without a new fact.
- **What was asked:** download content the user's own paid membership grants
  access to, using their account.
- **What we found:**
  - **No scoped or revocable credential exists.** yt-dlp refuses OAuth for
    YouTube outright (`_base.py:691-693`), so the only mechanisms are a cookie
    file or reading the browser's store. A YouTube cookie is a **full-account
    session** — there is no read-only variant.
  - **The risk is the account, not the file.** Automated authenticated requests
    are what enforcement looks at, and a Google suspension takes the whole
    account. File hygiene does nothing about that.
  - **"Only this channel" cannot be enforced.** The grant and the identity are
    the same cookie; authority lives on the server.
  - **DRM stays impossible** regardless (ADR-0003).
- **Why it was withdrawn:** the only safe shape is an expendable account with
  its own paid membership. The user's free-month offer sits on an account they
  would rather keep, and — their words — shipping `--cookies` would put every
  future user's account in play for a feature that cannot be made safe by
  design. For a tool whose promise is "nothing of yours is put at risk", that is
  the correct call.
- **What this means:** nothing changes. Rule 4 stands unamended, no ADR-0011 is
  owed, `cookiefile` and `cookiesfrombrowser` stay banned package-wide
  (`tests/test_security.py:176-205`). `docs/cookies.md` was removed, because it
  documented a feature that will not exist; the README carries a one-line
  limitation so users do not file it as a bug.
- **What the user does instead:** records the members stream with OBS while the
  membership is active. Outside the tool, and no credential anywhere near
  vicxiun.
- **Condition to revisit:** only if YouTube ships a credential scoped to one
  channel or to members-only access. At that point the account risk disappears
  and this entry becomes obsolete rather than wrong.
- **Shape if it is ever revisited** (kept because it was the easy part):
  `--cookies FILE`, per run, never stored in settings, never read by vicxiun —
  the path goes to yt-dlp and must reach **both** the resolver and the executor,
  or members videos fail during planning. File outside the repo and outside
  synced folders. `cookiesfrombrowser`, `--username` and `--password` stay
  banned. The file grant is the whole account; say so once, in the docs.

### 8.6 Rate limiting: bulk downloads have no way to be slowed down

- **Raised 2026-10-25** while checking yt-dlp's own guidance (the investigation
  that led to §8.5, which has since been withdrawn — this item stands on its
  own).
- yt-dlp's wiki gives hard numbers: ~300 videos/hour for a guest session,
  ~2000 videos/hour for an account; exceeding it yields
  `This content isn't available, try again later`, and the documented fix is
  5-10 seconds between items via the sleep options.
- **vicxiun exposes none of them** — no `--sleep-requests`, no
  `--sleep-interval`, no `--limit-rate`. So a large `--from-file` list has no
  in-tool way to be gentle, and the failure it produces reads like a content
  problem.
- **Recommendation:** add one passthrough — `--sleep-requests SECONDS`
  (fractional allowed; yt-dlp's own default is 0.75) into the executor
  options, plus a README line. Skip `--limit-rate`: it slows a transfer
  without changing request cadence, which is what the limit is about.
- Cheap: one argparse entry, one options key, one test asserting the key
  reaches `_build_executor_options`. Decision owed.
