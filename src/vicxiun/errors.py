"""Named errors and the exit-code table.

Every failure path yields a named error and a stable exit code. The
``vicxiun`` command line catches the names here and maps them to the right
exit value.

The exit codes are part of the project's public contract; downstream
scripts rely on them.
"""

from __future__ import annotations


class UserError(Exception):
    """A failure the user can fix: bad arguments, bad link, bad path.

    Exit code: 2 -- the conventional argparse convention and the value
    tests assert on.
    """


class PrerequisiteMissing(Exception):
    """A Prerequisite program is missing; the tool refuses to proceed.

    Exit code: 3.
    """


class RefusedContent(Exception):
    """DRM / paywalled content. A feature, not a failure.

    Exit code: 4.
    """


class PartialSuccess(Exception):
    """Some Items succeeded; some did not.

    Exit code: 1 -- distinct from "everything worked" (0) and "nothing
    worked" (5).
    """


class UnexpectedFailure(Exception):
    """A failure without a better-named cause.

    Exit code: 5.
    """


EXIT_CODE: dict[type[Exception], int] = {
    UserError: 2,
    PrerequisiteMissing: 3,
    RefusedContent: 4,
    PartialSuccess: 1,
    UnexpectedFailure: 5,
}


def exit_code_for(exc: BaseException) -> int:
    """Return the exit code for an exception, walking the MRO.

    Defaults to 5 ("unexpected") for anything we do not name.
    """
    for cls in type(exc).__mro__:
        code = EXIT_CODE.get(cls)
        if code is not None:
            return code
    return 5
