#!/usr/bin/env python3
"""The sole installed Codex hook entry point; delivers a registered session's callbacks to its collector."""
import argparse
import json
from pathlib import Path
import sys

from berman.capture.runtime import Collector, capture, marker_warning



def route(event, home, root):
    home, root = Path(home), Path(root)
    sid = event.get('session_id')
    if not isinstance(sid, str) or not capture.SESSION.fullmatch(sid):
        raise ValueError('Invalid session ID')
    prompt = event.get('prompt', '')
    first = next((s for s in prompt.splitlines() if s.strip()), '') if isinstance(prompt, str) else ''
    opening = event.get('hook_event_name') == 'UserPromptSubmit' and first.startswith(Collector.marker)
    path = home / 'berman-capture' / (sid + '.json')
    state = capture.read_json(path) if path.exists() else None
    if state and state.get('retired'):
        state = None
    if state and state.get('workspace') not in (None, str(root)):
        return {}
    if opening:
        run = first[len(Collector.marker):]
    elif state:
        run = state['run']
    else:
        return marker_warning(prompt) if event.get('hook_event_name') == 'UserPromptSubmit' else {}
    if not capture.RUN.fullmatch(run):
        raise ValueError('Use an exact fieldwork marker and run-NNN as the first nonblank line')
    record_path = root / 'operations' / run / 'operation.json'
    record = capture.read_json(record_path) if record_path.exists() else {}
    recorded_root = record.get('workspace', {}).get('root')
    if recorded_root and Path(recorded_root) != root:
        raise ValueError(f'{run}: workspace differs from its reservation; restore the recorded installation')
    recorded_home = record.get('harness', {}).get('home')
    if recorded_home and Path(recorded_home) != home:
        raise ValueError(f'{run}: Codex home differs from its reservation; restore the recorded installation')
    if record.get('capture', {}).get('adapter') != Collector.adapter:
        raise ValueError(f'{run} belongs to another collector')
    packet = record.get('packet')
    if not packet:
        raise ValueError(f'{run} has no recorded packet')
    return Collector(Path(packet).parent, home, root).hook(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('hook',))
    parser.add_argument('--codex-home', type=Path, default=capture.CODEX_HOME)
    parser.add_argument('--analysis-root', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = route(json.load(sys.stdin), args.codex_home, args.analysis_root)
    except Exception as error:
        result = {'systemMessage': 'Berman fieldwork capture: ' + str(error)}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
