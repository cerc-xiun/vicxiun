# vicxiun

vicxiun keeps finished videos and live streams as playable files on the machine
its owner already has. This glossary fixes the project's shared vocabulary, so
that code, tests, tickets and documentation all name the same things the same
way.

## Language

### The work

**Job**:
All of the Items asked for in one command, handled together from first to last.
_Avoid_: batch, session, task, run

**Item**:
One link, treated as exactly one thing to handle: either a Download or a
Capture.
_Avoid_: entry, request, task, video

**Playlist**:
A link that resolves to many videos at once. Not the Queue — the Queue is
the tool's own sequential handling of the links you typed. The tool asks
before handling a Playlist above the playlist limit. A Playlist's videos
land in one folder of their own, one file per video (ticket #23).
_Avoid_: channel, mix, collection

**Download**:
An Item whose link points at a finished video, kept as one playable file
holding both picture and sound.
_Avoid_: fetch, grab, save

**Capture**:
An Item whose link points at a live stream, recorded from its beginning until
it is deliberately stopped, and left playable even when it ends unexpectedly.
_Avoid_: recording, stream download, rip

**Queue**:
The strictly sequential handling of a Job's Items, one at a time and in order.
A waiting Capture blocks it; running one in a separate terminal is the
documented workaround.
_Avoid_: playlist, pool, batch, parallel processing

**Skip record**:
The file kept beside the output that remembers which Items were already
finished, so re-running the same Queue skips them instead of fetching them
again. It is not a history: it holds only what is done, one link per line.
_Avoid_: history, log, archive, cache

### What you ask for

**Profile**:
A named choice of what an Item should produce — phone-safe (the default), best,
audio or capture — independent of the link itself.
_Avoid_: preset, mode, quality, format

**Plan**:
What the tool intends to do with one Item: whether it is a Download or a
Capture, its Profile, the format chosen for it, and the absolute path its file
would take. The tool prints the Plan and then carries it out; a preview prints
it and stops.
_Avoid_: dry run, forecast

### The outside world

**Uploader**:
The source an Item came from, and the group its file is filed under. An Item
whose Uploader name is missing or unusable goes to a shared fallback group
instead.
_Avoid_: channel, creator, author, account

**Prerequisite**:
A program outside this project that the tool needs in order to finish an Item,
such as a media muxer or a JavaScript runtime. Prerequisites are detected and
explained, never installed for the user.
_Avoid_: dependency, requirement, helper, tool

**Pre-flight floor**:
The free space a volume must have before a Capture starts. Below it the
Capture does not start, and the message says what is required and what is
available. A Download is not subject to it.
_Avoid_: minimum space, disk check, space limit

**Running floor**:
The free space below which an Item mid-transfer is stopped cleanly — the
same shutdown path as a deliberate stop — so a Capture still leaves a
playable file.
_Avoid_: low-disk warning, cutoff, kill
