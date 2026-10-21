# The skip record is a plain file beside the output, keyed by exact URL

Ticket #14 skips Items the user already has. The record of what is done is a
plain text file (`.vicxiun-done.txt`) in the output folder itself, one URL per
line, matched against the link exactly as the user typed it. That placement and
that key are what make re-running a Queue both safe and free: the skip check
runs before any resolve, so a fully completed Queue's re-run downloads nothing
and performs no metadata request at all.

An Item whose target file already exists is skipped too, and is then recorded —
so that run's later re-runs skip the resolve as well.

**_Rejected_**: recording resolved video ids (or target paths) instead of URLs.
The id only exists after a network resolve, so every skip decision -- even for
an Item finished long ago -- would cost a request. The exact URL string costs
nothing and is what the Queue already holds.

**_Rejected_**: reusing yt-dlp's `--download-archive`. It keys on extractor plus
id and is only consulted during extraction, after the resolve; it could not
deliver the free second run, and it would couple vicxiun's skip semantics to
yt-dlp's file format.
