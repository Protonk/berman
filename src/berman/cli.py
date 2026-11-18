"""Read and verify Berman evidence, and reach the controller and executor that conduct fieldwork.

Fieldwork is commissioned, prepared, launched, ended, collected and preserved only through the
controller and executor: `berman remote` operates a guest study and `berman executor` manages
native staging. The other commands read. They check the installed software, portable recordings
and reading editions, and they read a co-located workspace: a directory with berman.json made by
release 0.26.3 or earlier, which is read-only in this version. Anything unsettled in one has to be
closed with the release that opened it.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import zlib

from berman import presentation
from berman import records
from berman import verify
from berman import workspace
from berman.files import Decision

# Commands of the co-located writer that releases up to 0.26.3 carried.
RETIRED = (
    'init',
    'ready',
    'new',
    'launch',
    'finish',
    'supplement',
    'cancel',
    'dev',
    'cheatsheet',
    'capture',
    'support-trial',
)
REMOVED = (
    'berman {name} is not part of this version. Fieldwork is commissioned and closed only through the controller '
    'and executor (berman remote --help). A co-located workspace is read-only here: anything outstanding in one '
    'has to be closed with the release that opened it.'
)
NO_WORKSPACE = (
    'This command reads a co-located workspace, a directory with berman.json made by release 0.26.3 or earlier. '
    'Select one with --root or run inside it. A guest study is operated with berman remote.'
)


def show(args, kind, result):
    """Print a result: JSON when asked or when no presentation is defined, otherwise for a person."""
    if args.json or kind is None:
        print(json.dumps(result, indent=2))
    else:
        presentation.show(kind, result)


def command_word(argv):
    """The first word that is neither a global option nor the value of --root."""
    words = iter(argv)
    for word in words:
        if word == '--root':
            next(words, None)
        elif not word.startswith('-'):
            return word
    return None


def refuse(message, as_json):
    if as_json:
        print(json.dumps({'state': 'needs-attention', 'message': message}, indent=2), file=sys.stderr)
    else:
        print('Berman needs attention: ' + message, file=sys.stderr)
    return 2


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['remote']:
        from berman.connector import main as remote_main

        return remote_main(argv[1:])
    if argv[:1] == ['executor']:
        from berman.executor import main as executor_main

        return executor_main(argv[1:])
    name = command_word(argv)
    if name in RETIRED:
        return refuse(REMOVED.format(name=name), '--json' in argv[: argv.index(name)])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--root', type=Path, help='co-located workspace to read; otherwise discovered from the current directory'
    )
    parser.add_argument('--json', action='store_true', help='machine-readable result')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser(
        'remote', help='commission, launch, end and preserve fieldwork in a guest study (berman remote --help)'
    )
    commands.add_parser('executor', help='manage native staging (berman executor --help)')
    commands.add_parser('version', help='show the software version and source identity')
    commands.add_parser(
        'status',
        help='report what a co-located workspace holds and what in it is unsettled; changes no file and closes nothing',
    )
    verification = commands.add_parser(
        'verify',
        help='check the installed software, or a co-located workspace as evidence, and name what remains '
        'unestablished; read-only unless --record',
    )
    verification.add_argument('--software', action='store_true', help='check only the standalone software')
    verification.add_argument(
        '--skip',
        action='append',
        default=[],
        choices=verify.SKIPPABLE,
        help='leave a slow check unrun (repeatable); the verdict is then incomplete',
    )
    verification.add_argument(
        '--record',
        metavar='experiments/NAME',
        help='keep the result and raw receipts in this new folder under experiments/',
    )
    archive = commands.add_parser(
        'archive',
        help='build or verify a reading edition of a co-located workspace, or verify one published edition by --path',
    )
    archive.add_argument('--verify', action='store_true')
    archive.add_argument(
        '--freshness',
        action='store_true',
        help='compare published inputs with current inputs separately from integrity',
    )
    archive.add_argument('--path', type=Path, help='verify this immutable edition outside any workspace')
    archive.add_argument(
        '--edition',
        choices=('connections', 'stewart'),
        help="default: the workspace's edition setting in berman.json, otherwise connections",
    )
    archive.add_argument('--connection', action='append', default=[])
    archive.add_argument('--run', action='append', default=[])
    recording = commands.add_parser(
        'recording', help='verify or materialize a portable recording, or export one from a co-located workspace'
    )
    recording_commands = recording.add_subparsers(dest='recording_command', required=True)
    for name in ('verify', 'materialize'):
        sub = recording_commands.add_parser(name)
        sub.add_argument('path', type=Path)
        sub.add_argument('--sha256', help='expected package digest from a trusted separate record')
        if name == 'materialize':
            sub.add_argument('destination', type=Path)
    exported = recording_commands.add_parser(
        'export', help='write a new package from a preserved run; leave the co-located workspace unchanged'
    )
    exported.add_argument('--run', required=True)
    exported.add_argument('--output', type=Path, required=True)
    exported.add_argument('--historical', action='store_true', help='retain explicitly missing historical authority')
    exported.add_argument(
        '--collection-id', help='explicit agreed UUID for a historical source without collection.json'
    )
    args = parser.parse_args(argv)
    try:
        if args.command == 'version':
            print(json.dumps(workspace.identity(), indent=2))
            return 0
        if args.command == 'recording' and args.recording_command in ('verify', 'materialize'):
            from berman import evidence

            if args.recording_command == 'materialize':
                result = evidence.materialize(args.path, args.destination, args.sha256)
            elif args.path.is_dir():
                if args.sha256:
                    raise Decision('--sha256 names a package digest; verify the ZIP before materializing it')
                result = evidence.verify_directory(args.path)
            else:
                result = evidence.verify_package(args.path, args.sha256)
            show(args, None, result)
            return 0
        if args.command == 'archive' and args.path:
            from berman.archive.builder import main as archive_main

            if args.freshness or args.edition or args.connection or args.run:
                raise Decision('--path verifies one published edition; selection and freshness need a workspace')
            archive_main(['--verify', '--path', str(args.path)])
            return 0
        selected = args.root or workspace.discover()
        if args.command == 'verify' and (args.software or selected is None):
            if args.record:
                raise Decision('--record requires a workspace verification')
            result = verify.software(skip=args.skip)
            show(args, 'verification', result)
            return verify.EXIT[result['verdict']]
        if selected is None:
            raise Decision(NO_WORKSPACE)
        if args.command == 'archive':
            from berman.archive.builder import main as archive_main

            options = ['--root', str(selected), '--edition', args.edition or workspace.edition(selected)]
            options += ['--verify'] if args.verify else []
            options += ['--freshness'] if args.freshness else []
            for kind in ('connection', 'run'):
                for value in getattr(args, kind):
                    options += ['--' + kind, value]
            archive_main(options)
            return 0
        if args.command == 'verify':
            # Before the workspace loads, so a broken berman.json is reported as a check, not a refusal.
            result = verify.verify(selected, skip=args.skip, record=args.record)
            show(args, 'verification', result)
            return verify.EXIT[result['verdict']]
        store = records.Store(selected)
        if args.command == 'recording':
            from berman import recordings

            result = recordings.export(store, args.run, args.output, args.historical, args.collection_id)
            show(args, None, result)
            return 0
        show(args, 'status', records.status(store))
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        RuntimeError,
        EOFError,
        zipfile.BadZipFile,
        zlib.error,
        subprocess.SubprocessError,
    ) as exc:
        return refuse(str(exc), args.json)


if __name__ == '__main__':
    raise SystemExit(main())
