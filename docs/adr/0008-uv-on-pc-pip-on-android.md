# uv on PC, pip on Android

vicxiun is published as one package, and the documented install command differs
by platform: uv on a PC, pip inside Termux on Android. uv has no official
Android build, so pip is not a preference there but the only option; naming
both commands keeps a user from guessing which tool they are supposed to have,
and keeps the difference to the installation step rather than the engine.

**_Rejected_**: documenting a single installer for both platforms. uv
everywhere is not possible on Android today, and pip everywhere on a PC would
give up the environment and dependency handling that make uv the better tool
there.
