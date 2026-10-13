# vicxiun

vicxiun keeps finished videos and live streams as playable files on the machine
its owner already has. This glossary fixes the project's shared vocabulary, so
that code, tests, tickets and documentation all name the same things the same
way.

## Language

### The work

**Job**:
All of the Items asked for in one run of the tool, handled together from first
to last.
_Avoid_: batch, session, task, run

**Item**:
One link, treated as exactly one thing to handle: either a Download or a
Capture.
_Avoid_: entry, request, task, video

**Download**:
An Item whose link points at a finished video, kept as a single file with its
picture and sound already together.
_Avoid_: fetch, grab, save

**Capture**:
An Item whose link points at a live stream, recorded from its beginning until
it is deliberately stopped, and left playable even when it ends unexpectedly.
_Avoid_: recording, stream download, rip

**Queue**:
The strictly sequential handling of a Job's Items: one at a time, in order,
skipping what is already on disk and continuing past an Item that fails. A
Capture waiting for its stream to begin blocks the Queue.
_Avoid_: playlist, pool, batch, parallel processing

### What you ask for

**Profile**:
A named choice of what an Item should produce — phone-safe (the default), best,
audio or capture — independent of the link itself.
_Avoid_: preset, mode, quality, format

**Plan**:
What the tool intends to do with one Item: whether it is a Download or a
Capture, its Profile, the format chosen for it, and the absolute path its file
would take. A real run prints the Plan and carries it out; a preview prints it
and stops.
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
