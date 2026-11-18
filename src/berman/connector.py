"""Operate the guest study from a native terminal using the configured OpenSSH client."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid

from berman import cheatsheet, executor, files, protocol, study


def ssh_client(settings):
    host = settings['host']
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', host):
        raise ValueError('Use a configured SSH host alias')
    argv = ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=10']
    if settings.get('ssh_config'):
        argv += ['-F', str(Path(settings['ssh_config']).expanduser())]
    return [*argv, '--', host]


class Remote:
    def __init__(self, settings, *, controller_python='.local/berman/bin/python'):
        self.collection = protocol.identifier(settings['collection_id'])
        self.study = study.identity(settings['study'], 'study')
        self.argv = [
            *ssh_client(settings),
            shlex.quote(controller_python),
            '-I',
            '-m',
            'berman.controller',
            '--study',
            self.study,
        ]

    def call(self, command, operation=None, body=None, **fields):
        request = dict(protocol=protocol.VERSION, collection_id=self.collection, command=command, **fields)
        if operation:
            request['operation'] = operation
        header = protocol.json_bytes(request) + b'\n'
        if len(header) > protocol.HEADER_LIMIT:
            raise ValueError('Command header exceeds protocol bound')
        with subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
            try:
                with process.stdin:
                    process.stdin.write(header)
                    if body:
                        with body.open('rb') as stream:
                            shutil.copyfileobj(stream, process.stdin, length=1024 * 1024)
            except BrokenPipeError:
                pass
            reply = process.stdout.read(protocol.HEADER_LIMIT + 1)
            if len(reply) > protocol.HEADER_LIMIT:
                process.kill()
                raise ValueError('Controller reply exceeds protocol bound')
            if process.wait():
                # The guest's own reason, if any, is already on stderr above this line.
                raise ValueError(
                    f'Guest command {command} did not complete. If the guest gave a reason above, act on it; '
                    'if the connection failed, repeat the same command.'
                )
        value = json.loads(reply)
        if (value.get('protocol'), value.get('collection_id'), value.get('command')) != (
            protocol.VERSION,
            self.collection,
            command,
        ):
            raise ValueError('Unexpected controller receipt')
        if operation and (
            value.get('operation') != operation or value.get('request_sha256') != protocol.fingerprint(request)
        ):
            raise ValueError('Controller receipt does not match the submitted command')
        return value


def stable_id(collection, *parts):
    return str(uuid.uuid5(uuid.UUID(collection), json.dumps(parts)))


def assignment_cache(spool, collection, run):
    """Where this host keeps the assignment it fetched for a run, beside the executor's staging."""
    return Path(spool) / 'controller-receipts' / collection / run / 'assignment.json'


def programs(prefix=()):
    """The words that run this installation's two commands again: (operator, standalone).

    A printed command is meant to be pasted, and a workstation can hold another `berman`. A bare
    name is used only when PATH finds this same installation under it; otherwise the full path is.
    """

    def word(path):
        found = shutil.which(path.name)
        return path.name if found and Path(found).resolve() == path.resolve() else str(path)

    script = Path(sys.argv[0])
    if script.name == 'berman-research' and script.is_file():
        return [word(script)], [word(script.resolve().parent / 'berman')]
    if script.name == 'berman' and script.is_file():
        return [word(script), *prefix], [word(script)]
    return [sys.executable, '-m', 'berman', 'remote'], [sys.executable, '-m', 'berman']


class Operator:
    def __init__(self, settings, remote=None, command=None):
        self.settings = settings
        self.remote = remote or Remote(settings)
        # The words that reach this study again, for the next step shown to the operator after prepare.
        self.command = command
        self.spool = Path(settings['spool']).expanduser().resolve()

    def commission(self, args):
        operation = stable_id(self.remote.collection, 'commission', args.request)
        return self.remote.call(
            'commission',
            operation,
            connection=args.connection,
            recipe=args.recipe,
            plan=args.plan,
            harness=args.harness,
        )

    def action(self, command, run, supplement=None, key=None, **fields):
        """One mutation with a stable identity, so a repeat after a lost reply is recognized.

        The identity is the run, the command, the member path for a transfer, a caller's key
        (a sitting number), and the supplement number. Requests for the first collection keep
        the identity and fields they always had.
        """
        parts = [run, command, fields.get('path')]
        if key is not None:
            parts.append(key)
        if supplement is not None:
            parts.append(f'supplement-{supplement}')
            fields['supplement'] = supplement
        return self.remote.call(command, stable_id(self.remote.collection, *parts), run=run, **fields)

    def read(self, command, run, supplement=None, **fields):
        if supplement is not None:
            fields['supplement'] = supplement
        return self.remote.call(command, run=run, **fields)

    def ticket(self, run):
        cache = assignment_cache(self.spool, self.remote.collection, run)
        if cache.is_file():
            return files.read(cache)
        value = self.remote.call('assignment', run=run)['result']
        files.keep(cache, files.json_bytes(value))
        return value

    def report_sitting(self, run, observed):
        """Send one finished sitting's receipt. The first is the launch; later ones are numbered."""
        number = observed.get('sitting', 1)
        if number == 1:
            return self.action('launch', run, observation=observed)
        return self.action('sitting', run, key=f'sitting-{number}', number=number, observation=observed)

    def collect(self, run, supplement, assignment):
        declaration = self.read('closeout', run, supplement)['result']['operation']
        for observed in executor.sitting_receipts(self.spool, assignment['operation']):
            if observed.get('exit') is not None:
                self.report_sitting(run, observed)
        return executor.collect(self.spool, assignment['operation'], declaration, supplement)

    def transfer(self, run, supplement, held):
        """Only send outstanding members. Read the frozen inventory once for the batch."""
        unavailable = []
        if held['outstanding']:
            operation = self.ticket(run)['operation']
            declaration = self.read('closeout', run, supplement)['result']['operation']
            try:
                folder, manifest = executor.frozen_manifest(self.spool, operation, declaration, supplement)
            except OSError as error:
                unavailable = [dict(path=name, problem=str(error)) for name in held['outstanding']]
            else:
                members = protocol.inventory(manifest['members'])
                for name in held['outstanding']:
                    if name not in members:
                        raise ValueError(f'Frozen host inventory does not list the guest member: {name}')
                    size, digest = members[name]
                    try:
                        source = executor.verify_payload(folder, dict(path=name, size=size, sha256=digest))
                    except (OSError, ValueError) as error:
                        unavailable.append({'path': name, 'problem': str(error)})
                        continue
                    self.action('transfer', run, supplement, body=source, path=name, size=size, sha256=digest)
        result = (
            self.read('status', run, supplement, deep=True) if unavailable else self.read('status', run, supplement)
        )
        result['unavailable_on_host'] = unavailable
        return result

    def closeout(self, args):
        run, supplement = args.run, args.supplement
        if args.reason is not None:
            self.action('ending', run, reason=args.reason)
        state = self.read('status', run, supplement)['result']
        if supplement is None and 'ending' not in state['facts']:
            raise ValueError('Supply --reason with the researcher’s ending declaration to start closeout.')
        if 'capture' not in state['facts']:
            observation = self.collect(run, supplement, self.ticket(run))
            self.action('capture', run, supplement, observation=observation)
            state = self.read('status', run, supplement)['result']

        report = dict(
            collection_id=self.remote.collection,
            study=self.settings['study'],
            run=run,
            supplement=supplement,
            sittings=state['sittings'],
            notes=sum(n.startswith('packet/notes/') for n in state['observed']),
            observed_members=len(state['observed']),
            limitations=state['capture_limits'],
            omitted=state['omitted'],
        )
        if not state['preserved']:
            state = self.read('status', run, supplement, deep=True)['result']
            if state['outstanding']:
                transferred = self.transfer(run, supplement, state)
                state = transferred['result']
                if state['outstanding'] and (not args.missing_reason or state['accepted']):
                    return dict(
                        report,
                        status='transfer incomplete',
                        outstanding=state['outstanding'],
                        unavailable=transferred['unavailable_on_host'],
                        already_accepted=bool(state['accepted']),
                    )
            if not state['accepted']:
                for line in report['limitations']:
                    print(f'Capture limitation: {line}', file=sys.stderr)
                for row in report['omitted']:
                    print(f'Omitted: {row["path"]} ({row["detail"]})', file=sys.stderr)
                for name in state['outstanding']:
                    print(f'Missing from transfer: {name}', file=sys.stderr)
                extra = {'missing_reason': args.missing_reason} if state['outstanding'] else {}
                state['accepted'] = self.action('accept', run, supplement, **extra)['result']
            state['preserved'] = self.action('preserve', run, supplement)['result']
        if not state['published']:
            state['published'] = self.action('publish', run, supplement)['result']
        verified = self.read('verify', run)['result']
        if supplement is not None:
            verified = next(row for row in verified['supplements'] if row['sequence'] == supplement)
        return dict(
            report,
            status='verified',
            accepted_members=state['accepted']['accepted_members'],
            missing=state['accepted'].get('missing', []),
            missing_reason=state['accepted'].get('missing_reason'),
            recording=f'~/research/{self.settings["study"]}/{state["preserved"]["relative_path"]}',
            recording_sha256=verified['recording']['package_sha256'],
            edition=verified['edition']['archive_path'],
            edition_sha256=verified['edition']['archive_sha256'],
            coverage=verified['recording']['coverage'],
        )

    def perform(self, args):
        if args.action == 'status':
            if args.run is None:
                return self.remote.call('status', run=args.run)
            return self.read('status', args.run, args.supplement)
        if args.action == 'commission':
            return self.commission(args)
        if not re.fullmatch(r'run-[0-9]{3,6}', args.run):
            raise ValueError('Invalid run identity')
        supplement = getattr(args, 'supplement', None)
        if args.action == 'closeout':
            return self.closeout(args)
        if args.action == 'verify':
            return self.remote.call('verify', run=args.run)
        if args.action == 'exposure':
            op = stable_id(self.remote.collection, args.run, 'exposure', args.request)
            return self.remote.call('exposure', op, run=args.run, account=args.account)
        if args.action == 'supplement':
            op = stable_id(self.remote.collection, args.run, 'supplement', args.request)
            return self.remote.call('supplement', op, run=args.run, reason=args.reason)
        if args.action == 'end':
            return self.action('ending', args.run, reason=args.reason)
        if args.action == 'accept':
            extra = {} if args.missing_reason is None else {'missing_reason': args.missing_reason}
            return self.action('accept', args.run, supplement, **extra)
        if args.action in ('preserve', 'publish'):
            return self.action(args.action, args.run, supplement)
        a = self.ticket(args.run)
        if args.action == 'prepare':
            binding = self.settings['bindings'][a['connection']][a['harness']]
            prepared = executor.prepare(self.spool, a, dict(binding, connection=a['connection']))
            receipt = self.action('preparation', args.run, observation=prepared)
            if self.command:
                print(cheatsheet.prepared_note(prepared, self.command), file=sys.stderr)
            return receipt
        if args.action in ('launch', 'resume'):
            observed = executor.launch(
                self.spool,
                a['operation'],
                one_turn=args.one_turn,
                resume=args.action == 'resume',
                prompt=getattr(args, 'prompt', None),
                request=getattr(args, 'request', None),
            )
            if observed.get('exit') is None:
                raise ValueError(
                    'The sitting is recorded as started and its exit is not yet observed. Retry when it ends.'
                )
            return self.report_sitting(args.run, observed)
        if args.action == 'qualify':
            declaration = self.read('closeout', args.run, supplement)['result']['operation']
            return executor.qualify(self.spool, a['operation'], declaration, args.expected_child)
        if args.action in ('collect', 'transfer'):
            observation = self.collect(args.run, supplement, a)
            if args.action == 'collect':
                return self.action('capture', args.run, supplement, observation=observation)
            held = self.read('status', args.run, supplement, deep=True)['result']
            if 'capture' not in held['facts']:
                raise ValueError('The guest has no inventory for this collection yet. Run collect first.')
            return self.transfer(args.run, supplement, held)
        raise ValueError('Unknown operator command')


def closeout_text(report):
    label = report['run']
    if report['supplement'] is not None:
        label += f' supplement {report["supplement"]}'
    lines = [
        f'{report["study"]}/{label}: {report["status"]}. Sittings: {report["sittings"]}. Note files observed: {report["notes"]}.'
    ]
    if report['status'] != 'verified':
        problems = {row['path']: row['problem'] for row in report['unavailable']}
        lines.extend(
            f'Outstanding: {name} ({problems.get(name, "missing in guest")})' for name in report['outstanding']
        )
        lines.extend(report['limitations'])
    else:
        lines.extend(
            [
                f'Accepted {report["accepted_members"]} of {report["observed_members"]} observed files.',
                f'Guest recording: {report["recording"]}',
                f'Guest reading edition: {report["edition"]}',
            ]
        )
        lines.extend(report['coverage']['unknown'])
    return '\n'.join(lines)


def study_command(config):
    """The operator command for the study these settings select, naming the settings unless they are the default."""
    selected = [] if config == Path(cheatsheet.DEFAULT_CONFIG).expanduser() else ['--config', cheatsheet.shown(config)]
    return [*programs(('remote',))[0], *selected]


def sheet(config, remote=None):
    """The cheatsheet for the study `config` selects, or for an installation with none. Reads only.

    It sends one `status` to the guest. An unreachable guest is reported in the sheet, not raised.
    """
    operator, standalone = programs(('remote',))
    if not config.is_file():
        front = [*standalone, 'cheatsheet', '--config'] if operator[-1] == 'remote' else [*operator, '--config']
        tail = [] if operator[-1] == 'remote' else ['cheatsheet']
        folder = Path(cheatsheet.DEFAULT_CONFIG).expanduser().parent
        others = []
        for path in sorted(folder.glob('*.json')) if folder.is_dir() else []:
            try:
                value = files.read(path)
            except OSError, ValueError:
                continue
            if isinstance(value, dict) and {'study', 'collection_id', 'host'} <= set(value):
                others.append(path)
        choices = [[*front, cheatsheet.shown(other), *tail] for other in others]
        return cheatsheet.unconfigured(config, choices, standalone)
    settings = files.read(config.resolve(strict=True))
    remote = remote or Remote(settings)
    spool = Path(settings['spool']).expanduser().resolve()
    try:
        rows, problem = remote.call('status', run=None)['result'], None
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        rows, problem = None, str(error)

    def operation_of(run):
        cache = assignment_cache(spool, remote.collection, run)
        return files.read(cache)['operation'] if cache.is_file() else None

    return cheatsheet.study(settings, study_command(config), config, rows, problem, operation_of, standalone)


def arguments():
    """The operator command line. The cheatsheet's printed commands are checked against it."""
    parser = argparse.ArgumentParser(prog='berman remote', description=__doc__)
    parser.add_argument(
        '--config',
        type=Path,
        default=Path(os.environ.get('BERMAN_CONNECTOR', cheatsheet.DEFAULT_CONFIG)).expanduser(),
        help='connector settings for one study (default: $BERMAN_CONNECTOR, then %(default)s)',
    )
    commands = parser.add_subparsers(dest='action', required=True)
    front = commands.add_parser(
        'cheatsheet', help='print the commands that apply to this study now, ready to paste; changes nothing'
    )
    front.add_argument('--json', action='store_true', help='machine-readable result')
    commission = commands.add_parser('commission', help='reserve or recover one immutable guest assignment')
    commission.add_argument(
        '--request', required=True, help='a unique label for this research decision; reuse on retry'
    )
    commission.add_argument('--connection', required=True)
    commission.add_argument('--harness', required=True, choices=('codex', 'claude'))
    origin = commission.add_mutually_exclusive_group()
    origin.add_argument('--recipe')
    origin.add_argument('--plan')
    status = commands.add_parser(
        'status', help='recorded facts; with a run, also what is observed, transferred and outstanding'
    )
    status.add_argument('run', nargs='?')
    status.add_argument('--supplement', type=int, help='report one supplement of the run')
    helps = {
        'prepare': 'stage the worker packet for the bound checkout and report the preparation',
        'launch': 'start the first sitting and wait for it; repeating this never starts another process',
        'resume': 'start a further sitting of the same engagement, resuming its native session',
        'closeout': 'end fieldwork and resume through a verified recording and reading edition',
        'end': 'record the researcher ending; a native exit never does this',
        'supplement': 'declare later activity after a frozen collection; returns its number',
        'collect': 'freeze what the host holds for the ending, or for --supplement N',
        'transfer': 'send every frozen member the guest lacks; unreadable staged files are listed, not fatal',
        'accept': 'have the guest verify what it holds and accept it into custody',
        'preserve': 'build the immutable recording from accepted evidence',
        'publish': 'publish the reading edition of a preserved recording',
        'verify': 'reread the preserved recordings and editions in the guest',
        'qualify': 'evaluate the first collection as a native support trial (local; nothing is recorded)',
        'exposure': 'add a dated researcher account of an exposure to the run',
    }
    for action, text in helps.items():
        cmd = commands.add_parser(action, help=text)
        cmd.add_argument('run')
        if action == 'closeout':
            origin = cmd.add_mutually_exclusive_group()
            origin.add_argument('--reason', help='researcher’s ending declaration; omit to resume a recorded ending')
            origin.add_argument('--supplement', type=int, help='close out an already declared supplement')
            cmd.add_argument('--json', action='store_true', help='print a machine-readable closeout summary')
        if action == 'qualify':
            cmd.add_argument('--expected-child')
        if action == 'exposure':
            cmd.add_argument('--account', required=True)
            cmd.add_argument('--request', required=True)
        if action in ('end', 'supplement'):
            cmd.add_argument('--reason', required=True)
        if action == 'supplement':
            cmd.add_argument('--request', required=True, help='a unique label for this declaration; reuse on retry')
        if action in ('collect', 'transfer', 'accept', 'preserve', 'publish'):
            cmd.add_argument('--supplement', type=int, help='act on this supplement instead of the first collection')
        if action in ('closeout', 'accept'):
            cmd.add_argument(
                '--missing-reason',
                help='accept what arrived although observed members are missing; the reason and the members are recorded',
            )
        if action in ('launch', 'resume'):
            cmd.add_argument('--one-turn', action='store_true', help='run a noninteractive native qualification')
        if action == 'resume':
            cmd.add_argument('--prompt', help='text to send on resuming; required with --one-turn')
            cmd.add_argument(
                '--request',
                required=True,
                help='a label for this sitting; repeating the command with it never starts another',
            )
    return parser


def main(argv=None):
    args = arguments().parse_args(argv)
    try:
        if args.action == 'cheatsheet':
            value = sheet(args.config)
            print(json.dumps(value, indent=2) if args.json else cheatsheet.text(value))
            return 0
        settings = files.read(args.config.resolve(strict=True))
        result = Operator(settings, command=study_command(args.config)).perform(args)
        if args.action == 'closeout':
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(closeout_text(result))
            if result['status'] != 'verified':
                print('Restore the unavailable evidence and repeat closeout.', file=sys.stderr)
                if not result['already_accepted']:
                    print(
                        'To accept the remaining evidence, repeat with --missing-reason and the researcher’s reason.',
                        file=sys.stderr,
                    )
                return 1
            return 0
        print(json.dumps(result, indent=2))
        if args.action == 'qualify' and not result['passed']:
            return 1
        if args.action == 'transfer' and result['unavailable_on_host']:
            print(
                f'Berman connector: {len(result["unavailable_on_host"])} staged member(s) could not be read on this host. '
                'Everything else was sent. To take what arrived into custody, run accept with --missing-reason.',
                file=sys.stderr,
            )
            return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'Berman connector: {error}', file=sys.stderr)
        if args.action == 'closeout':
            print('After resolving the error, repeat closeout for the same run.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
