"""What an operator sees: finished results in, text out. Nothing here computes state or a verdict.

There are two renderings of the same results. `plain` is the established text, byte for byte
what version 0.4 printed: a pipe, a file and anything that reads Berman's output receive it.
`terminal` is for an interactive terminal and uses Rich. `show` chooses between them. `--json`
output is printed by the command line itself and never comes through here.

Only the command line imports this package, and this package imports no other part of Berman;
pyproject.toml's Import Linter contracts hold both.
"""

import os
import sys

from berman.presentation import plain

KINDS = ('status', 'cheatsheet', 'verification', 'prepared')


def interactive(stream):
    """Whether stream is a terminal that can show styled text: a tty, and not a dumb one."""
    try:
        return bool(stream.isatty()) and os.environ.get('TERM') not in ('dumb', 'unknown')
    except AttributeError, ValueError:
        return False


def show(kind, result, stream=None):
    """Print one command's result for a person; redirected output is the plain text, unchanged."""
    if kind not in KINDS:
        raise ValueError(f'No presentation is defined for {kind}')
    stream = sys.stdout if stream is None else stream
    if interactive(stream):
        from berman.presentation import terminal

        terminal.render(kind, result, terminal.console(stream))
    else:
        print(getattr(plain, kind)(result), file=stream)
