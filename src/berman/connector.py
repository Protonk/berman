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
                raise ValueError(f'SSH command {command} failed. Retry with the same request identity.')
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
    def __init__(self, settings):
        self.settings = settings
        self.remote = Remote(settings)
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

    def action(self, command, run, **fields):
        op = stable_id(self.remote.collection, run, command, fields.get('path'))
        return self.remote.call(command, op, run=run, **fields)

    def ticket(self, run):
        cache = self.receipts / run / 'assignment.json'
        if cache.is_file():
            return files.read(cache)
        value = self.remote.call('assignment', run=run)['result']
        files.keep(cache, files.json_bytes(value))
        return value

    def perform(self, args):
        if args.action == 'status':
            return self.remote.call('status', run=args.run)
        if args.action == 'commission':
            return self.commission(args)
        if not re.fullmatch(r'run-[0-9]{3,6}', args.run):
            raise ValueError('Invalid run identity')
        if args.action == 'verify':
            return self.remote.call('verify', run=args.run)
        if args.action == 'exposure':
            op = stable_id(self.remote.collection, args.run, 'exposure', args.request)
            return self.remote.call('exposure', op, run=args.run, account=args.account)
        if args.action == 'end':
            return self.action('ending', args.run, reason=args.reason)
        if args.action in ('accept', 'preserve', 'publish'):
            return self.action(args.action, args.run)
        a = self.ticket(args.run)
        if args.action == 'prepare':
            binding = self.settings['bindings'][a['connection']][a['harness']]
            prepared = executor.prepare(self.spool, a, dict(binding, connection=a['connection']))
            return self.action('preparation', args.run, observation=prepared)
        if args.action == 'launch':
            observed = executor.launch(self.spool, a['operation'], one_turn=args.one_turn)
            if observed.get('exit') is None:
                raise ValueError('Launch intent already exists and process exit is unobserved. Inspect native staging.')
            return self.action('launch', args.run, observation=observed)
        if args.action in ('collect', 'transfer', 'qualify'):
            token = self.remote.call('closeout', run=args.run)['result']
            ending_id = token['operation']
            if args.action == 'qualify':
                return executor.qualify(self.spool, a['operation'], ending_id, args.expected_child)
            observation = executor.collect(self.spool, a['operation'], ending_id)
            if args.action == 'collect':
                return self.action('capture', args.run, observation=observation)
            previous = self.remote.call('status', run=args.run)['result']['transferred']
            rows = protocol.inventory(observation['members'])
            for name, (size, digest) in rows.items():
                if previous.get(name) != digest:
                    source = executor.payload_path(self.spool, a['operation'], ending_id, name)
                    self.action('transfer', args.run, body=source, path=name, size=size, sha256=digest)
            return self.remote.call('status', run=args.run)
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
    commands.add_parser('status').add_argument('run', nargs='?')
    for action in (
        'prepare',
        'launch',
        'end',
        'collect',
        'transfer',
        'accept',
        'preserve',
        'publish',
        'verify',
        'qualify',
        'exposure',
    ):
        cmd = commands.add_parser(action)
        cmd.add_argument('run')
        if action == 'qualify':
            cmd.add_argument('--expected-child')
        if action == 'exposure':
            cmd.add_argument('--account', required=True)
            cmd.add_argument('--request', required=True)
        if action == 'end':
            cmd.add_argument('--reason', required=True)
        if action == 'launch':
            cmd.add_argument('--one-turn', action='store_true', help='run a noninteractive native qualification')
    args = parser.parse_args(argv)
    try:
        result = Operator(files.read(args.config.resolve(strict=True))).perform(args)
        print(json.dumps(result, indent=2))
        if args.action == 'qualify' and not result['passed']:
            return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'Berman connector: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
