"""A small operator interface to Berman's preparation and preservation workflow."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile
import zlib

from berman.capture import adapters
from berman import presentation
from berman import support as support_trials
from berman import verify
from berman import workspace
from berman.files import Decision
from berman.records import Store
from berman.workflow import Workflow


def show(args, kind, result):
    """Print a result: JSON when asked or when no presentation is defined, otherwise for a person."""
    if args.json or kind is None:
        print(json.dumps(result, indent=2))
    else:
        presentation.show(kind, result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='Fieldwork workspace; otherwise discover from the current directory')
    parser.add_argument('--json', action='store_true', help='machine-readable result')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='create a workspace with isolated harness homes; assign no fieldwork')
    init.add_argument('destination', type=Path)
    init.add_argument('--project', type=Path, required=True)
    init.add_argument('--connection', default='project')
    init.add_argument('--codex', type=Path)
    init.add_argument('--claude', type=Path)
    commands.add_parser('version', help='show the software version and source identity')
    archive = commands.add_parser('archive', help='build or verify a reading edition')
    archive.add_argument('--verify', action='store_true')
    archive.add_argument('--freshness', action='store_true', help='compare published inputs with current inputs separately from integrity')
    archive.add_argument('--path', type=Path, help='verify this immutable edition outside any workspace')
    archive.add_argument('--edition', choices=('connections', 'stewart'),
                         help="default: the workspace's edition setting in berman.json, otherwise connections")
    archive.add_argument('--connection', action='append', default=[])
    archive.add_argument('--run', action='append', default=[])

    recording = commands.add_parser('recording', help='verify, export or materialize a self-contained recording')
    recording_commands = recording.add_subparsers(dest='recording_command', required=True)
    for name in ('verify', 'materialize'):
        sub = recording_commands.add_parser(name)
        sub.add_argument('path', type=Path)
        sub.add_argument('--sha256', help='expected package digest from a trusted separate record')
        if name == 'materialize':
            sub.add_argument('destination', type=Path)
    exported = recording_commands.add_parser('export', help='write a new package; leave the source workspace unchanged')
    exported.add_argument('--run', required=True)
    exported.add_argument('--output', type=Path, required=True)
    exported.add_argument('--historical', action='store_true', help='retain explicitly missing historical authority')
    exported.add_argument('--collection-id', help='explicit agreed UUID for a historical source without collection.json')
    imported = recording_commands.add_parser('import', help='retain a historical manifest and package beside unchanged originals')
    imported.add_argument('--run', required=True)

    commands.add_parser('status', help='discover runs across connections and their next agreed roles without changing files')
    commands.add_parser('cheatsheet', help='print the commands that start fieldwork and development sessions now, '
                                           'from the recorded situation; changes nothing')

    def selecting(name, help_text):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument('--connection', help='connection to operate within; an explicit --run determines its own')
        return sub
    ready = selecting('ready', 'check readiness for the next task without assigning or starting a session')
    ready.add_argument('--development', choices=('codex', 'claude'), help='check the installed developer launcher receipt')
    new = selecting('new', 'settle authorized preservation and prepare the next fieldworker')
    new.add_argument('--repeat', metavar='RUN', help='repeat the retained instructions of this run with fresh project context')
    new.add_argument('--recipe', help='directly assign this recipe; on retry, check the retained recipe id')
    disposition = new.add_mutually_exclusive_group()
    disposition.add_argument('--finish-previous', metavar='REASON', help='declare the single previous engagement finished')
    disposition.add_argument('--keep-open', action='store_true', help='retain existing engagements for continuation')
    finish = selecting('finish', 'declare and preserve the single open engagement')
    finish.add_argument('--reason', required=True)
    finish.add_argument('--run')
    finish.add_argument('--allow-incomplete', metavar='REASON', help='explicitly retain an interrupted or incomplete boundary')
    supplement = selecting('supplement', 'preserve later activity without ending the engagement or changing its frozen capture')
    supplement.add_argument('--reason', required=True)
    supplement.add_argument('--run')
    launch = selecting('launch', 'verify and launch the prepared fieldworker')
    launch.add_argument('--run')
    cancel = selecting('cancel', 'retain an unused preparation without advancing the instruction sequence')
    cancel.add_argument('--reason', required=True)
    cancel.add_argument('--run')
    dev = selecting('dev', 'start ordinary development; creates no fieldwork assignment and consumes no plan step')
    dev.add_argument('--harness', choices=('codex', 'claude'), required=True)
    dev.add_argument('--check', action='store_true', help='verify the development launcher without starting development; '
                     'Claude uses a short model session')
    dev.add_argument('arguments', nargs=argparse.REMAINDER, help='options forwarded to the configured development launcher')
    capture = commands.add_parser('capture', help='capture adapter installation and verification')
    capture_commands = capture.add_subparsers(dest='capture_command', required=True)
    readiness = capture_commands.add_parser('readiness', help='check an adapter against its recorded verification basis')
    readiness.add_argument('--adapter', choices=tuple(adapters.ADAPTERS), required=True)
    install = capture_commands.add_parser('install', help="add an adapter's hook definition to Codex hooks.json; grants no trust")
    install.add_argument('--adapter', choices=('berman-codex',), required=True)
    generated = capture_commands.add_parser('definition', help='print the Codex hook definition generated from berman.json; '
                                                               'with --write, keep it as the workspace template')
    generated.add_argument('--adapter', choices=('berman-codex',), required=True)
    generated.add_argument('--write', action='store_true', help='write hooks/berman-capture.hooks.json; installs nothing')
    trust = capture_commands.add_parser('record-trust', help='record an actual /hooks review in the Codex CLI')
    trust.add_argument('--evidence', type=Path, required=True, help='terminal record of the /hooks review')
    trust.add_argument('--adapter', choices=('codex', 'berman-claude'), default='codex')
    trust.add_argument('--trial', help='Claude support trial whose per-launch hooks were reviewed')
    trust.add_argument('--attest', action='store_true', required=True,
                       help='I reviewed /hooks in the actual CLI; every installed capture event is enabled and Trusted')
    trust.add_argument('--statement', default='Operator reviewed /hooks in the actual Codex CLI.')
    linked = capture_commands.add_parser('record-capture', help="link a collected support trial's native capture to its "
                                                                "adapter's trust basis for the same bytes")
    linked.add_argument('--trial', required=True, metavar='experiments/NAME', help='the support trial folder')
    support = commands.add_parser('support-trial', help='native capture support trials; never research, and they '
                                                        'consume no research-plan step')
    support_commands = support.add_subparsers(dest='support_command', required=True)
    for name, help_text in (('prepare', 'reserve a support identity and prepare its packet, project and profile'),
                            ('launch', 'start the one interactive session inside a recorded terminal'),
                            ('collect', 'verify the native capture, then finalize and preserve it in the trial folder'),
                            ('cleanup', 'retire the closed registration, keep the profile, remove an empty project')):
        sub = support_commands.add_parser(name, help=help_text)
        sub.add_argument('--evidence', required=True, metavar='experiments/NAME',
                         help='the trial folder under experiments/; new for prepare')
        if name == 'prepare':
            sub.add_argument('--adapter', choices=tuple(adapters.ADAPTERS), required=True)
        elif name == 'launch':
            sub.add_argument('--model', help='Claude support trial model; no effect on the later fieldworker')
            sub.add_argument('--max-budget-usd', type=float, help='Required bounded budget for a Claude support trial')
        elif name == 'collect':
            sub.add_argument('--allow-incomplete', metavar='REASON',
                             help='preserve an intentional incomplete boundary; the trial then demonstrates no capture')
        elif name == 'cleanup':
            sub.add_argument('--reason', required=True)
    verification = commands.add_parser('verify', help='run the maintained checks and name what remains unestablished; '
                                                      'read-only unless --record or --native-access')
    verification.add_argument('--software', action='store_true', help='check only the standalone software')
    verification.add_argument('--skip', action='append', default=[], choices=verify.SKIPPABLE,
                              help='leave a slow check unrun (repeatable); the verdict is then incomplete')
    verification.add_argument('--native-access', action='store_true',
                              help='also run the real Codex sandbox check for each connection in disposable roots')
    verification.add_argument('--record', metavar='experiments/NAME',
                              help='keep the result and raw receipts in this new folder under experiments/')
    args = parser.parse_args(argv)
    workflow = None
    try:
        if args.command in ('init', 'version'):
            result = (workspace.identity() if args.command == 'version' else
                      workspace.initialize(args.destination, args.project, args.connection, args.codex, args.claude))
            print(json.dumps(result, indent=2))
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
            if args.native_access or args.record:
                raise Decision('--native-access and --record require a workspace verification')
            result = verify.software(skip=args.skip)
            show(args, 'verification', result)
            return verify.EXIT[result['verdict']]
        if selected is None:
            raise Decision('Select a fieldwork workspace with --root, or create one with berman init')
        args.root = selected
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
            result = verify.verify(args.root, skip=args.skip, record=args.record,
                                          native=verify.native_access if args.native_access else None)
            show(args, 'verification', result)
            return verify.EXIT[result['verdict']]
        store = Store(args.root)
        if args.command == 'recording':
            from berman import recordings
            if args.recording_command == 'import':
                result = recordings.publish(store, args.run, historical=True)
            else:
                result = recordings.export(store, args.run, args.output, args.historical, args.collection_id)
            show(args, None, result)
            return 0
        workflow = Workflow(store)
        if args.command == 'status':
            result = workflow.status()
        elif args.command == 'cheatsheet':
            result = workflow.cheatsheet()
        elif args.command == 'new':
            result = workflow.new(args.finish_previous, args.keep_open, args.connection, recipe_name=args.recipe, repeat=args.repeat)
        elif args.command == 'finish':
            result = workflow.finish(args.reason, args.run, args.allow_incomplete, args.connection)
        elif args.command == 'supplement':
            result = workflow.supplement(args.reason, args.run, args.connection)
        elif args.command == 'cancel':
            result = workflow.cancel(args.reason, args.run, args.connection)
        elif args.command == 'ready':
            result = workflow.ready(args.connection, args.development)
        elif args.command == 'dev':
            arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
            if args.check:
                arguments = ['--check', *arguments]
            start = workflow.dev(args.harness, arguments, args.connection)
            os.chdir(start['cwd'])
            print(f'Ordinary development in {start["cwd"]} ({start["connection"]}); no fieldwork record is made.', flush=True)
            os.execv(start['argv'][0], start['argv'])
        elif args.command == 'capture':
            if args.capture_command == 'readiness':
                result = adapters.readiness(store, args.adapter)
            elif args.capture_command == 'install':
                result = adapters.install(store, args.adapter)
            elif args.capture_command == 'definition':
                result = adapters.template(store, args.adapter, args.write)
            elif args.capture_command == 'record-capture':
                result = support_trials.record_capture(store, args.trial)
            else:
                if args.adapter == 'berman-claude':
                    from berman.harnesses.claude_support import record_trust
                    result = record_trust(store, args.trial, args.evidence, args.statement)
                else:
                    result = adapters.record_trust(store, args.evidence, args.statement)
        elif args.command == 'support-trial':
            if args.support_command == 'launch':
                result = support_trials.launch(store, args.evidence, args.model, args.max_budget_usd)
                if result is None:
                    return 0
            elif args.support_command == 'prepare':
                result = support_trials.prepare(store, args.evidence, args.adapter)
            elif args.support_command == 'collect':
                result = support_trials.collect(store, args.evidence, args.allow_incomplete)
            else:
                result = support_trials.cleanup(store, args.evidence, args.reason)
        else:
            workflow.launch(args.run, args.connection)
            return 0
        show(args, {'new': 'prepared', 'cheatsheet': 'cheatsheet', 'status': 'status', 'finish': 'finished', 'ready': 'ready'}.get(args.command), result)
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, EOFError, zipfile.BadZipFile, zlib.error, subprocess.SubprocessError) as exc:
        result = {'state': 'needs-attention', 'message': str(exc)}
        if workflow:
            try:
                result['progress'] = workflow.progress()
            except (OSError, ValueError, KeyError, TypeError) as progress_error:
                result['progress_error'] = str(progress_error)
            result['request'] = str(workflow.request_path) if workflow.request_path else None
        if args.json:
            print(json.dumps(result, indent=2), file=sys.stderr)
        else:
            for row in result.get('progress', []):
                print(f'Recorded progress: {row["run"]}: {row["state"]}', file=sys.stderr)
            print('Berman needs attention: ' + str(exc), file=sys.stderr)
            if result.get('request'):
                print('Request retained at ' + result['request'] + '; retry this command or berman new.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
