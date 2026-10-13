# One engine, two install paths

vicxiun has exactly one engine — one piece of code that fetches, selects and
writes media — and the PC and Android installations both run it. The platforms
differ in how the tool is installed and in a small amount of environment
detection around it; those differences are real but narrow, and confining them
to one place means a bug in the download and capture logic exists once rather
than once per platform.

**_Rejected_**: a separate engine, or a platform-specific fork of the download
logic, for Android. It would double every fix and hide one platform's breakage
behind the other platform's passing tests, for differences that never touch how
media is chosen or written.
