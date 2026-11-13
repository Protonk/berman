"""What an operator sees: finished results in, text out. Nothing here computes state or a verdict.

There are two renderings of the same results. `plain` is the established text, byte for byte
what version 0.4.1 printed, and it is the default everywhere: on a terminal, in a pipe, in a
file. `terminal` is a Rich rendering for an interactive terminal. It is used only when the
operator asks for it by setting BERMAN_OUTPUT=rich, and then only when standard output is an
interactive terminal, so redirected output is the plain text whatever is set. `--json` output is
printed by the command line itself and never comes through here.

Only the command line imports this package, and this package imports no other part of Berman;
pyproject.toml's Import Linter contracts hold both.
"""

import os
import sys

from berman.presentation import plain

KINDS = ('status', 'cheatsheet', 'verification', 'prepared')
CHOICE = 'BERMAN_OUTPUT'  # Set to 'rich' to select the terminal rendering; anything else is plain.


def interactive(stream):
    """Whether stream is a terminal that can show styled text: a tty, and not a dumb one."""
    try:
        return bool(stream.isatty()) and os.environ.get('TERM') not in ('dumb', 'unknown')
    except AttributeError, ValueError:
        return False


def show(kind, result, stream=None):
    """Print one command's result for a person: the plain text, unless Rich was asked for and can be shown."""
    if kind not in KINDS:
        raise ValueError(f'No presentation is defined for {kind}')
    stream = sys.stdout if stream is None else stream
    if os.environ.get(CHOICE) == 'rich' and interactive(stream):
        try:
            from berman.presentation import terminal
        except ImportError:
            terminal = None  # A damaged environment still gets its report, as plain text.
        if terminal is not None:
            terminal.render(kind, result, terminal.console(stream))
            return
    print(getattr(plain, kind)(result), file=stream)
