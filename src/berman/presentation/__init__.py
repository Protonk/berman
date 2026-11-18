"""What an operator sees: finished results in, text out. Nothing here computes state or a verdict.

There are two renderings of the same results. `plain` is the established text, pinned byte for
byte by tests/fixtures/presentation: a pipe, a file and anything that reads Berman's output receive it.
`terminal` is a Rich rendering, used when standard output is an interactive terminal. An
operator who wants the plain text there too sets BERMAN_OUTPUT=plain. `--json` output is
printed by the command line itself and never comes through here.

Only the command line imports this package, and this package imports no other part of Berman;
pyproject.toml's Import Linter contracts hold both.
"""

import os
import sys

from berman.presentation import plain

KINDS = ('status', 'verification')
CHOICE = 'BERMAN_OUTPUT'  # Set to 'plain' to keep the plain text on an interactive terminal.


def interactive(stream):
    """Whether stream is a terminal that can show styled text: a tty, and not a dumb one."""
    try:
        return bool(stream.isatty()) and os.environ.get('TERM') not in ('dumb', 'unknown')
    except AttributeError, ValueError:
        return False


def show(kind, result, stream=None):
    """Print one command's result for a person; anything but an interactive terminal gets the plain text."""
    if kind not in KINDS:
        raise ValueError(f'No presentation is defined for {kind}')
    stream = sys.stdout if stream is None else stream
    if interactive(stream) and os.environ.get(CHOICE) != 'plain':
        try:
            from berman.presentation import terminal
        except Exception:  # Import-time failures must not hide the result we already computed.
            terminal = None  # A damaged environment still gets its report, as plain text.
        if terminal is not None:
            terminal.render(kind, result, terminal.console(stream))
            return
    print(getattr(plain, kind)(result), file=stream)
