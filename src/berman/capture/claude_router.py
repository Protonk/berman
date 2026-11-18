#!/usr/bin/env python3
"""Per-launch Claude hooks: record the reserved parent session."""
import argparse
import json
from pathlib import Path
import sys

from berman import files
from berman.capture.claude import ClaudeCollector
from berman._legacy.api import RUN


def route(event, root, run):
    if not RUN.fullmatch(run):
        raise ValueError('Invalid run identity')
    record = files.read(root / 'operations' / run / 'operation.json')
    if record.get('run') != run or record.get('capture', {}).get('adapter') != 'berman-claude':
        raise ValueError('Hook is not assigned to this Claude run')
    # Subagents may inherit settings. Only the explicitly reserved parent is captured.
    if event.get('session_id') != record['harness']['session_id']:
        return {}
    collector = ClaudeCollector(Path(record['packet']).parent, record['harness']['home'], root).for_run(run)
    return collector.hook(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('capture',))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin)
        result = route(event, args.root, args.run)
    except Exception as exc:
        print(f'Berman Claude capture failed: {exc}', file=sys.stderr)
        result = {'systemMessage': f'Berman Claude capture failed: {exc}'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
