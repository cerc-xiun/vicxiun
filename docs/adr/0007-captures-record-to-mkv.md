# Captures are recorded to MKV

A Capture writes MKV while a Download writes MP4. A live stream cannot be
resumed, so if a capture is stopped or interrupted the file must still open —
and an interrupted MP4 frequently does not, while MKV survives being cut off
mid-way. The container is the entire safety net for a Capture, which is why it
differs from the container used for a Download that can simply be run again.

**_Rejected_**: recording captures to MP4 like Downloads. It is the format
phones and editors prefer, and remuxing a finished Capture to MP4 was set aside
for a later version — but neither is worth a multi-hour capture that will not
open.
