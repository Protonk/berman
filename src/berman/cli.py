"""Operate guest fieldwork, verify the installed software and read guest recordings and editions."""

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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['remote']:
        from berman.connector import main as remote_main

        return remote_main(argv[1:])
    if argv[:1] == ['executor']:
        from berman.executor import main as executor_main

        return executor_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='machine-readable result')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('remote', help='operate a guest study (berman remote --help)')
    commands.add_parser('executor', help='manage native staging (berman executor --help)')
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
    args = parser.parse_args(argv)
    try:
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
        if args.command != 'verify':
            parser.error(f'place {args.command} first to pass options to it')
        result = verify.software()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for name, check in result['checks'].items():
                print(f'{name} {"passed" if check["passed"] else "failed"}: {check["detail"]}')
        return 0 if result['passed'] else 1
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
