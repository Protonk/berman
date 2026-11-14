"""A disposable workflow child that can die without running Python exception cleanup."""

import json
import os
from pathlib import Path
import signal
import sys

from berman.records import Store
from berman.workflow import Workflow
from tests.support.workspace import FixtureHarness


def main():
    root, mode = Path(sys.argv[1]), sys.argv[2]
    connection = sys.argv[3] if len(sys.argv) > 3 else 'stewart'
    store = Store(root)
    replace, rename = os.replace, os.rename

    def kill():
        os.kill(os.getpid(), signal.SIGKILL)

    def interrupted_replace(source, destination, *args, **kwargs):
        target = str(destination)
        if mode == 'reserve-before' and target.endswith('/operation.json'):
            kill()
        if mode == 'closing-copy' and target.endswith('/context/berman/preparation/INSTRUCTIONS.md'):
            kill()
        if mode == 'capture-write' and target.endswith('/session/capture.json'):
            kill()
        replace(source, destination, *args, **kwargs)
        if mode == 'reserve-after' and target.endswith('/operation.json'):
            kill()
        if mode == 'after-index' and target.endswith('/source-layout.json'):
            kill()
        if mode == 'journal-after' and target.endswith('/migrations/run-001.json'):
            kill()

    def interrupted_rename(source, destination, *args, **kwargs):
        destination = Path(destination)
        quarantine = '/.state/recovery/' in str(destination) and destination.name == 'material'
        if mode == 'snapshot-before' and destination.name == 'initial-project':
            kill()
        if mode == 'quarantine-before' and quarantine:
            kill()
        rename(source, destination, *args, **kwargs)
        if mode == 'quarantine-after' and quarantine:
            kill()
        if mode == 'packet-after' and destination == store.field / 'run-001':
            kill()
        if mode == 'migration-after' and destination == root / 'runs/run-001':
            kill()

    os.replace, os.rename = interrupted_replace, interrupted_rename

    class Held(FixtureHarness):
        def preflight(self, connection=None):
            print('locked', flush=True)
            if sys.stdin.readline().strip() != 'continue':
                raise ValueError('parent did not release the synthetic worker')

    workflow = Workflow(store, Held(store) if mode == 'hold' else FixtureHarness(store))
    try:
        if mode in ('closing-copy', 'journal-after', 'migration-after', 'after-index'):
            result = workflow.finish('Explicit synthetic ending', 'run-001')
        elif mode == 'capture-write':
            result = store.collector.hook(
                {
                    'session_id': 'fixture-run-001',
                    'turn_id': 'two',
                    'hook_event_name': 'Stop',
                    'transcript_path': str(store.home / 'sessions/fixture-run-001.jsonl'),
                }
            )
        else:
            result = workflow.new(connection=connection)
        print(json.dumps(result), flush=True)
    except ValueError as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
