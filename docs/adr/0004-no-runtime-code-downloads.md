# No code is downloaded and executed at runtime

Everything the tool needs is declared in advance — yt-dlp and the package that
solves a site's JavaScript challenge — and a JavaScript runtime is required and
detected rather than fetched; yt-dlp's remote-components mechanism is not used.
Code fetched at runtime can change the tool's behaviour between two runs of the
same installed version, so the only way to keep the promise that vicxiun does
what was tested is to never fetch code at all.

**_Rejected_**: yt-dlp's remote-components mechanism, which fetches and runs
helper code on demand. It is convenient and would keep the tool working without
a reinstall — which is precisely the behaviour this project was built to
reverse after a prior script did the same thing.
