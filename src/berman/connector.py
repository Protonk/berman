"""Operate the guest study from a native terminal using the configured OpenSSH client."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

from berman import config, executor, files, protocol


class Remote:
    def __init__(self, settings):
        self.collection = protocol.identifier(settings['collection_id'])
        self.study = config.identity(settings['study'], 'study')
        host = settings['host']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', host):
            raise ValueError('Use a configured SSH host alias')
        self.argv = ['ssh', '-T', '-oBatchMode=yes', '-oConnectTimeout=10']
        if settings.get('ssh_config'):
            self.argv += ['-F', str(Path(settings['ssh_config']).expanduser())]
        self.argv += ['--', host, '.local/berman/bin/python', '-I', '-m', 'berman.controller', '--study', self.study]

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


class Operator:
    def __init__(self, settings, remote=None):
        self.settings = settings
        self.remote = remote or Remote(settings)
        self.spool = Path(settings['spool']).expanduser().resolve()
        self.receipts = self.spool / 'controller-receipts' / self.remote.collection

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

    def read(self, command, run, supplement=None):
        fields = {} if supplement is None else {'supplement': supplement}
        return self.remote.call(command, run=run, **fields)

    def ticket(self, run):
        cache = self.receipts / run / 'assignment.json'
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
            return self.action('preparation', args.run, observation=prepared)
        if args.action in ('launch', 'resume'):
            observed = executor.launch(
                self.spool,
                a['operation'],
                one_turn=args.one_turn,
                resume=args.action == 'resume',
                prompt=getattr(args, 'prompt', None),
            )
            if observed.get('exit') is None:
                raise ValueError(
                    'The sitting is recorded as started and its exit is not yet observed. Retry when it ends.'
                )
            return self.report_sitting(args.run, observed)
        if args.action in ('collect', 'transfer', 'qualify'):
            declaration = self.read('closeout', args.run, supplement)['result']['operation']
            if args.action == 'qualify':
                return executor.qualify(self.spool, a['operation'], declaration, args.expected_child)
            # Report every finished sitting first. Each has its own identity, so this repeats safely.
            for observed in executor.sitting_receipts(self.spool, a['operation']):
                if observed.get('exit') is not None:
                    self.report_sitting(args.run, observed)
            observation = executor.collect(self.spool, a['operation'], declaration, supplement)
            if args.action == 'collect':
                return self.action('capture', args.run, supplement, observation=observation)
            # Send each member the guest lacks. A staged file that is gone or changed is
            # reported and skipped, so one bad member never strands the rest on the host.
            outstanding = set(self.read('status', args.run, supplement)['result']['outstanding'])
            rows = protocol.inventory(observation['members'])
            unavailable = []
            for name, (size, digest) in rows.items():
                if name not in outstanding:
                    continue
                try:
                    source = executor.payload_path(self.spool, a['operation'], declaration, name, supplement)
                except (OSError, ValueError) as error:
                    unavailable.append({'path': name, 'problem': str(error)})
                    continue
                self.action('transfer', args.run, supplement, body=source, path=name, size=size, sha256=digest)
            result = self.read('status', args.run, supplement)
            result['unavailable_on_host'] = unavailable
            return result
        raise ValueError('Unknown operator command')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='berman remote', description=__doc__)
    parser.add_argument(
        '--config',
        type=Path,
        default=Path(os.environ.get('BERMAN_CONNECTOR', '~/.config/berman/connector.json')).expanduser(),
    )
    commands = parser.add_subparsers(dest='action', required=True)
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
        if action == 'accept':
            cmd.add_argument(
                '--missing-reason',
                help='accept what arrived although observed members are missing; the reason and the members are recorded',
            )
        if action in ('launch', 'resume'):
            cmd.add_argument('--one-turn', action='store_true', help='run a noninteractive native qualification')
        if action == 'resume':
            cmd.add_argument('--prompt', help='text to send on resuming; required with --one-turn')
    args = parser.parse_args(argv)
    try:
        result = Operator(files.read(args.config.resolve(strict=True))).perform(args)
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
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
