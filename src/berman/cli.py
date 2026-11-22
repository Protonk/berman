"""Intermittent project management in a local Linux checkout."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from berman import capture, episodes, files, records
from berman.installation import JOB_TEMPLATES, PACKAGE, verify_wheel, version


def path(value):
    p = Path(value).expanduser()
    if p.is_symlink():
        raise argparse.ArgumentTypeError(f'Use the physical path: {p}')
    return p.resolve()


def parser():
    app = argparse.ArgumentParser(prog='berman', description=__doc__)
    commands = app.add_subparsers(dest='command', required=True)
    template = commands.add_parser(
        'template', help='create an editable additional job and the note-taking and handover examples'
    )
    template.add_argument('destination', type=path)
    opening = commands.add_parser('open', help='open an episode and fix the additional instructions')
    opening.add_argument('episode', type=path)
    opening.add_argument('--project', type=path, required=True, help='Git checkout for the ordinary work')
    opening.add_argument('--actor', choices=('codex', 'claude'), required=True)
    opening.add_argument(
        '--job',
        dest='jobs',
        metavar='FILE',
        type=path,
        action='append',
        required=True,
        help='UTF-8 instructions for one additional job. Repeat for each job in the opening.',
    )
    for name, meaning in [
        ('start', 'start the first interactive sitting'),
        ('resume', 'continue the same actor session'),
        ('status', 'show the opening, sittings, and ending'),
        ('close', 'record the manager’s ending and collect the return'),
    ]:
        cmd = commands.add_parser(name, help=meaning)
        cmd.add_argument('episode', type=path)
        if name == 'resume':
            cmd.add_argument('--request', required=True, help='stable nonempty label for this sitting')
            cmd.add_argument('--prompt', help='optional further instructions')
        if name == 'close':
            cmd.add_argument('--reason', required=True)
    verify = commands.add_parser('verify', help='identify the installation or verify an episode record')
    verify.add_argument('record', type=path, nargs='?')
    verify.add_argument('--sha256', help='expected digest of the manifest from a separate copy')
    review = commands.add_parser('review', help='prepare verified sources and an answer file for a manager’s question')
    review.add_argument('records', type=path, nargs='+')
    review.add_argument('--question', required=True)
    review.add_argument('--output', required=True, type=path)
    commands.add_parser('codex-hooks', help='print the Linux Codex hook entries for review and installation')
    commands.add_parser('version', help='identify the installed build')
    return app


def installation(check=False):
    paths = sorted(p for p in PACKAGE.rglob('*') if p.is_file() and p.suffix in ('.py', '.md'))
    members = {str(p.relative_to(PACKAGE)): files.digest(p.read_bytes()) for p in paths}
    if any(f'templates/{name}' not in members for name in JOB_TEMPLATES):
        raise ValueError('Incomplete installation: the job templates are missing')
    result = {'version': version(), 'source_sha256': files.digest_json(members), 'files_sha256': members}
    if check:
        result['verification'] = verify_wheel(members)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        match args.command:
            case 'template':
                result = episodes.install_template(args.destination)
            case 'open':
                result = episodes.open_episode(args.episode, args.project, args.actor, *args.jobs)
            case 'start':
                result = episodes.launch(args.episode)
            case 'resume':
                if not args.request.strip():
                    raise ValueError('Supply a nonempty request label')
                result = episodes.launch(args.episode, args.request, args.prompt)
            case 'status':
                result = episodes.status(args.episode)
            case 'close':
                result = episodes.close(args.episode, args.reason)
            case 'verify' if args.record:
                result = records.verify(args.record, args.sha256)
            case 'review':
                result = records.review(args.records, args.question, args.output)
            case 'codex-hooks':
                result = capture.hook_settings('codex')
            case _:
                if getattr(args, 'sha256', None):
                    raise ValueError('--sha256 needs a record directory')
                result = installation(check=args.command == 'verify')
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if args.command in ('start', 'resume'):
            if result.get('error'):
                return 1
            code = result.get('exit')
            if code:
                return 128 - code if code < 0 else code
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(f'berman: {exc}', file=sys.stderr)
        return 1
