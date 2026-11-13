#!/usr/bin/env python3
"""The sole installed owner of both fieldwork markers; delegates to frozen legacy or new capture."""
import argparse
import json
import os
from pathlib import Path
import sys

from berman.capture.runtime import Collector, LegacyCollector, capture, marker_warning



def route(event, home, root):
    home, root = Path(home), Path(root)
    sid = event.get('session_id')
    if not isinstance(sid, str) or not capture.SESSION.fullmatch(sid):
        raise ValueError('Invalid session ID')
    prompt = event.get('prompt', '')
    first = next((s for s in prompt.splitlines() if s.strip()), '') if isinstance(prompt, str) else ''
    adapters = (LegacyCollector, Collector)
    selected = next((c for c in adapters if event.get('hook_event_name') == 'UserPromptSubmit' and first.startswith(c.marker)), None)
    states = []
    for cls, directory in zip(adapters, ('stewart-fieldwork', 'berman-capture')):
        path = home / directory / (sid + '.json')
        state = capture.read_json(path) if path.exists() else None
        if state and not state.get('retired'):
            states.append((cls, state))
    if len(states) > 1:
        raise ValueError(f'Session {sid} has conflicting collector registrations; no capture was changed')
    if states and states[0][1].get('workspace') not in (None, str(root)):
        return {}
    if selected is None:
        if not states:
            return marker_warning(prompt) if event.get('hook_event_name') == 'UserPromptSubmit' else {}
        selected, state = states[0]
        run = state['run']
    else:
        run = first[len(selected.marker):]
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
    if record.get('capture', {}).get('adapter', 'legacy-codex') != selected.adapter:
        raise ValueError(f'{run} belongs to another collector')
    packet = record.get('packet')
    if packet:
        field = Path(packet).parent
    elif selected is LegacyCollector:
        config = capture.read_json(root / 'berman.json')
        field = Path(config.get('packet_root', config.get('fieldwork'))).expanduser()
        if not field.is_absolute():
            field = root / field
        field = Path(os.path.abspath(field))
    else:
        raise ValueError(f'{run} has no recorded packet')
    return selected(field, home, root).hook(event)


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
