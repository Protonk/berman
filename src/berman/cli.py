"""Find the command for what comes next, operate guest fieldwork, check the installed software and read recordings and editions."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import zlib

from berman import verify
from berman.files import Decision


def refuse(message, as_json):
    if as_json:
        print(json.dumps({'state': 'needs-attention', 'message': message}, indent=2), file=sys.stderr)
    else:
        print('Berman needs attention: ' + message, file=sys.stderr)
    return 2


def arguments():
    """The standalone command line. `remote` and `executor` have their own, in the connector and executor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='machine-readable result')
    commands = parser.add_subparsers(dest='command', required=True)
    sheet = commands.add_parser(
        'cheatsheet', help='start here: print the commands that apply now, ready to paste; changes nothing'
    )
    sheet.add_argument('--config', type=Path, help='connector settings for one study')
    sheet.add_argument('--json', action='store_true', dest='sheet_json', help='machine-readable result')
    commands.add_parser(
        'remote', help='operate a guest study; also installed as berman-research (berman remote --help)'
    )
    commands.add_parser('executor', help='manage native staging (berman executor --help)')
    ci = commands.add_parser('ci', help='exercise this core checkout on a qualification Mac and Linux guest')
    ci.add_argument('--config', required=True, type=Path, help='qualification SSH settings')
    ci.add_argument('--live', choices=('codex', 'claude'), help='launch the named harness in this terminal')
    commands.add_parser('version', help='show the installed version and source identity')
    commands.add_parser('verify', help='verify the installed software')
    archive = commands.add_parser('archive', help='verify a published guest reading edition')
    archive.add_argument('--path', type=Path, required=True, help='reading edition ZIP')
    recording = commands.add_parser('recording', help='verify or materialize a guest recording')
    recording_commands = recording.add_subparsers(dest='recording_command', required=True)
    for name in ('verify', 'materialize'):
        sub = recording_commands.add_parser(name)
        sub.add_argument('path', type=Path)
        sub.add_argument('--sha256', help='expected package digest from a trusted separate record')
        if name == 'materialize':
            sub.add_argument('destination', type=Path)
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['remote']:
        from berman.connector import main as remote_main

        return remote_main(argv[1:])
    if argv[:1] == ['executor']:
        from berman.executor import main as executor_main

        return executor_main(argv[1:])
    args = arguments().parse_args(argv)
    try:
        if args.command == 'cheatsheet':
            from berman.connector import main as remote_main

            selected = ['--config', str(args.config)] if args.config else []
            return remote_main([*selected, 'cheatsheet', *(['--json'] if args.json or args.sheet_json else [])])
        if args.command == 'ci':
            runner = Path(__file__).resolve().parents[2] / 'scripts/ci/run.py'
            if not runner.is_file():
                raise Decision('Run uv run berman ci from the core development checkout.')
            return subprocess.call(
                [sys.executable, '-I', runner, args.config.expanduser().resolve(), *([args.live] if args.live else [])]
            )
        if args.command == 'version':
            print(json.dumps(verify.identity(), indent=2))
            return 0
        if args.command == 'recording':
            from berman import evidence

            if args.recording_command == 'materialize':
                result = evidence.materialize(args.path, args.destination, args.sha256)
            elif args.path.is_dir():
                if args.sha256:
                    raise Decision('--sha256 names a package digest; verify the ZIP before materializing it')
                result = evidence.verify_directory(args.path)
            else:
                result = evidence.verify_package(args.path, args.sha256)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == 'archive':
            from berman.archive.package import verify_edition

            print(json.dumps(verify_edition(args.path), indent=2))
            return 0
        if args.command == 'verify':
            result = verify.software()
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                for name, check in result['checks'].items():
                    print(f'{name} {"passed" if check["passed"] else "failed"}: {check["detail"]}')
            return 0 if result['passed'] else 1
        if args.command in ('remote', 'executor'):
            # Reached only behind a global option; these two parse their own arguments.
            arguments().error(f'place {args.command} first to pass options to it')
        raise RuntimeError(f'berman {args.command} is declared in arguments() and not handled in main()')
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
