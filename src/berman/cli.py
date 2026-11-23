"""A producer uses Berman for intermittent project management by correspondence in a local Linux checkout."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from berman import capture, episodes, installation, records


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
    opening = commands.add_parser('open', help='open an episode and fix any additional jobs')
    opening.add_argument('episode', type=path)
    opening.add_argument('--project', type=path, required=True, help='Git checkout the actor works in')
    opening.add_argument('--actor', choices=tuple(capture.HARNESSES), required=True)
    opening.add_argument(
        '--job',
        dest='jobs',
        metavar='FILE',
        type=path,
        action='append',
        default=[],
        help='UTF-8 instructions for one additional job. Repeat for each job in the opening, or omit for none.',
    )
    for name, meaning in [
        ('start', 'start the first sitting with the opening'),
        ('resume', 'continue the same actor session in another sitting'),
        ('status', 'show the opening, sittings, and ending'),
        ('close', 'record the producer’s ending and collect the return'),
    ]:
        cmd = commands.add_parser(name, help=meaning)
        cmd.add_argument('episode', type=path)
        if name == 'resume':
            cmd.add_argument('--request', required=True, help='stable nonempty label for this sitting')
        if name == 'start':
            cmd.add_argument(
                '--request', help='label for another attempt at the opening while no native session has started'
            )
        if name in ('start', 'resume'):
            after = 'after the opening' if name == 'start' else 'in this sitting'
            cmd.add_argument('--prompt', help=f'message for the actor {after}; required with --unattended')
            cmd.add_argument(
                '--unattended',
                action='store_true',
                help='run the actor without its interactive interface and print the response it reports',
            )
        if name == 'close':
            cmd.add_argument('--reason', required=True)
    verify = commands.add_parser('verify', help='identify the installation or verify an episode record')
    verify.add_argument('record', type=path, nargs='?')
    verify.add_argument('--sha256', help='expected digest of the manifest from a separate copy')
    review = commands.add_parser('review', help='prepare verified sources and an answer file for a producer’s question')
    review.add_argument('records', type=path, nargs='+')
    review.add_argument('--question', required=True)
    review.add_argument('--output', required=True, type=path)
    commands.add_parser('codex-hooks', help='print the Linux Codex hook entries for review and installation')
    help_command = commands.add_parser('help', help='show help for Berman or one command')
    help_command.add_argument('topic', nargs='?', choices=commands.choices, metavar='COMMAND')
    help_command.set_defaults(topics=commands.choices)
    return app


def main(argv=None):
    app = parser()
    args = app.parse_args(argv)
    try:
        match args.command:
            case 'help':
                target = args.topics[args.topic] if args.topic else app
                target.print_help()
                return 0
            case 'template':
                result = episodes.install_template(args.destination)
            case 'open':
                result = episodes.open_episode(args.episode, args.project, args.actor, args.jobs)
            case 'start' | 'resume':
                opening = args.command == 'start'
                if args.request is not None and not args.request.strip():
                    raise ValueError('Supply a nonempty request label')
                if (args.unattended or opening) and args.prompt is not None and not args.prompt.strip():
                    raise ValueError('Supply a nonempty prompt')
                if args.unattended and args.prompt is None:
                    raise ValueError('An unattended sitting needs a message in --prompt')
                result = episodes.launch(args.episode, args.request, args.prompt, not args.unattended, opening)
            case 'status':
                result = episodes.status(args.episode)
            case 'close':
                result = episodes.close(args.episode, args.reason)
            case 'verify' if args.record:
                result = records.verify(args.record, args.sha256)
            case 'verify':
                if args.sha256:
                    raise ValueError('--sha256 needs a record directory')
                result = installation.verify()
            case 'review':
                result = records.review(args.records, args.question, args.output)
            case 'codex-hooks':
                result = capture.hook_settings('codex')
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
