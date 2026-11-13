#!/usr/bin/env python3
"""Connection-aware Codex capture through a separately verified hook entry point.

The legacy collector (capture_session.py) stays byte-for-byte unchanged and keeps the
Stewart-Fieldwork-Run marker. This collector claims only the Berman-Fieldwork-Run marker,
keeps its own registry, and registers a session only for a packet whose operation record names
this collector. Its packets carry no narrative.md placeholder, so the legacy collector refuses
them even if a session submits the legacy marker.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

from berman._legacy import capture_session as capture

MARKER = 'Berman-Fieldwork-Run: '
OWNER = 'berman-collector/1'
ADAPTER = 'berman-codex'
NEAR_MARKER = re.compile(r'(?im)^\s*(?:[*_>`~]+\s*)?Berman-Fieldwork-Run\s*:')
DESTINATIONS = re.compile(r'(run-[0-9]{3,6})/notes(?:/|`)')


class Collector(capture.Collector):
    def __init__(self, fieldwork, codex_home, analysis):
        super().__init__(fieldwork, codex_home, analysis)
        self.state = self.codex_home / 'berman-capture'
        self.legacy_state = self.codex_home / 'stewart-fieldwork'

    def run_path(self, run, readonly=False):
        if not capture.RUN.fullmatch(run):
            raise ValueError('Run must have the form run-004 (3 to 6 digits)')
        root = self.fieldwork / run
        if readonly and not root.exists():
            root = self.analysis / 'runs' / run
        if not root.is_dir() or any(p.is_symlink() for p in [root, *root.parents]):
            raise ValueError(f'Run directory must already exist without symlinks: {root}')
        capture.regular(root / 'INSTRUCTIONS.md')
        if not (root / 'notes').is_dir() or (root / 'notes').is_symlink():
            raise ValueError(f'Missing regular notes directory: {root}')
        return root

    def owner(self, run):
        """The run record must assign this packet to this collector at its canonical location."""
        path = self.analysis / 'operations' / run / 'operation.json'
        if not path.exists():
            raise ValueError(f'{run} has no Berman operation record; this collector does not register it')
        record = capture.read_json(path)
        if record.get('run') != run or (record.get('capture') or {}).get('adapter') != ADAPTER:
            raise ValueError(f'{run} is not assigned to the {OWNER} collector')
        if record.get('packet') != str(self.fieldwork / run):
            raise ValueError(f'{run} is recorded at another packet location; registration refused')
        return record

    def legacy_claim(self, sid):
        """An open legacy registration for the same session excludes this collector."""
        path = self.legacy_state / (sid + '.json')
        if not path.exists():
            return None
        state = capture.read_json(path)
        run = state.get('run')
        if state.get('retired') or not run:
            return None
        root = self.fieldwork / run
        if not root.is_dir() or (root / 'session/finalized.json').exists() or (root / 'session/ended.json').exists():
            return None
        return run

    def remember(self, sid, run, root, turn):
        capture.atomic(self.registry(sid), capture.json_bytes({
            'run': run, 'session_id': sid, 'active_turn': True, 'last_prompt_turn_id': turn,
            'workspace': str(self.analysis), 'harness': 'codex', 'collector': OWNER, 'packet': str(root)}))

    def register(self, event, run):
        sid = event.get('session_id')
        state_path = self.registry(sid)
        if self.archived(run):
            raise ValueError('Run number already exists in the archive; choose a new run number')
        self.owner(run)
        root = self.run_path(run)
        if any(value != run for value in DESTINATIONS.findall((root / 'INSTRUCTIONS.md').read_text())):
            raise ValueError('Recording instructions name another run; correct the destination paths before registering')
        for receipt in root.glob('session-history/*/replacement.json'):
            if (receipt.parent / 'session').exists() and capture.read_json(receipt)['session_id'] == sid:
                raise ValueError('This session was replaced; register the replacement from a fresh session')
        folder = root / 'session'
        if folder.is_symlink():
            raise ValueError('Session directory cannot be a symlink')
        if (folder / 'finalized.json').exists() or (folder / 'ended.json').exists():
            raise ValueError("This run's capture is closed; use a new run directory")
        binding_path = folder / 'binding.json'
        if binding_path.exists():
            if capture.read_json(binding_path)['session_id'] != sid:
                raise ValueError('This run is already bound to another session')
            self.remember(sid, run, root, event.get('turn_id'))
            return
        if folder.exists() and any(folder.iterdir()):
            raise ValueError('Session destination contains files without a collector binding')
        claimed = self.legacy_claim(sid)
        if claimed:
            raise ValueError(f'This session is already captured by the legacy collector for {claimed}; start a fresh session')
        old = capture.read_json(state_path) if state_path.exists() else None
        if old and old['run'] != run:
            old_root = Path(old.get('packet', self.fieldwork / old['run']))
            if old_root.is_dir():
                self.run_path(old['run'])
                if not (old_root / 'session/finalized.json').exists() and not (old_root / 'session/ended.json').exists():
                    prior = self.capture(old_root, {'hook_event_name': 'run-marker-changed'})
                    capture.atomic(old_root / 'session/ended.json', capture.json_bytes({
                        'ended_at_utc': capture.now(), 'next_run': run, 'next_start_turn_id': event.get('turn_id'),
                        'capture_sha256': prior['sha256'],
                        'meaning': 'Registration switched to another run; no later turns refresh this capture. The boundary prompt may be present in the full-session snapshot.'}))
        folder.mkdir(mode=0o700, exist_ok=True)
        binding = {'schema': 'stewart.session-binding/1', 'run': run, 'session_id': sid,
                   'registered_at_utc': capture.now(), 'start_turn_id': event.get('turn_id'),
                   'run_directory_at_registration': str(root), 'transcript_path': event.get('transcript_path'),
                   'marker': MARKER + run, 'collector': OWNER, 'workspace': str(self.analysis),
                   'cwd': event.get('cwd'), 'model': event.get('model'),
                   'scope': 'Full-session copies may contain earlier tasks; start_turn_id identifies this run\'s registration boundary.'}
        capture.atomic(binding_path, capture.json_bytes(binding))
        self.remember(sid, run, root, event.get('turn_id'))

    def at(self, packet_root):
        """A collector for a recorded packet root, sharing this registry and workspace."""
        return self if Path(packet_root) == self.fieldwork else Collector(packet_root, self.codex_home, self.analysis)

    def hook(self, event):
        name = event.get('hook_event_name')
        if name == 'UserPromptSubmit':
            prompt = event.get('prompt', '')
            if not isinstance(prompt, str):
                return {}
            state_path = self.registry(event.get('session_id'))
            if state_path.exists():
                with self.locked():
                    state = capture.read_json(state_path)
                    if not state.get('retired'):
                        state.update(active_turn=True, last_prompt_turn_id=event.get('turn_id'))
                        capture.atomic(state_path, capture.json_bytes(state))
            first = next((line for line in prompt.splitlines() if line.strip()), '')
            if not first.startswith(MARKER):
                if NEAR_MARKER.search(prompt):
                    return {'systemMessage': 'Berman fieldwork capture: no new registration. Use exactly Berman-Fieldwork-Run: run-NNN as the complete first nonblank line, without formatting.'}
                return {}
            run = first[len(MARKER):]
            if not capture.RUN.fullmatch(run):
                raise ValueError('Use exactly Berman-Fieldwork-Run: run-004 on the first nonblank line')
            path = self.analysis / 'operations' / run / 'operation.json'
            record = capture.read_json(path) if path.exists() else {}
            collector = self.at(Path(record['packet']).parent) if record.get('packet') else self
            with collector.locked():
                collector.register(event, run)
            return {}
        if name not in ('Stop', 'Interrupt', 'SessionEnd'):
            return {}
        state_path = self.registry(event.get('session_id'))
        if not state_path.exists():
            return {}
        with self.locked():
            state = capture.read_json(state_path)
            if state.get('retired') or state.get('workspace') != str(self.analysis):
                return {}
            run = state['run']
            collector = self.at(Path(state['packet']).parent)
            if not (collector.fieldwork / run).exists():
                if (self.analysis / 'runs' / run).is_dir():
                    return {}  # Migrated evidence stays immutable.
                return {'systemMessage': f'Berman fieldwork capture: active packet {run} is missing; it was not found in the archive either. Restore its directory before refreshing.'}
            root = collector.run_path(run)
            if (root / 'session/finalized.json').exists():
                if name == 'Stop' and not state.get('post_finalization_warned'):
                    state.update(post_finalization_warned=True, post_finalization_event=event.get('turn_id'), active_turn=False)
                    capture.atomic(state_path, capture.json_bytes(state))
                    return {'systemMessage': f'Berman fieldwork capture: {run} is finalized. This later Stop cannot extend it. Use berman supplement to preserve additional activity.'}
                return {}
            record = collector.capture(root, event)
            state.update(active_turn=False, last_terminal_event=name)
            capture.atomic(state_path, capture.json_bytes(state))
            claimed = collector.legacy_claim(event.get('session_id'))
            if claimed:
                return {'systemMessage': f'Berman fieldwork capture: this session is also registered by the legacy collector for {claimed}; inspect both runs.'}
            if record.get('last_assistant_message_present') is False:
                return {'systemMessage': 'Berman capture saved, but the final assistant message was not yet found. Refresh before migration.'}
        return {}


def collector(run, codex_home, analysis):
    """Resolve a run's collector through its recorded packet location."""
    from berman.capture.runtime import Collector as RoutedCollector
    record = capture.read_json(analysis / 'operations' / run / 'operation.json')
    if record.get('harness', {}).get('home', str(codex_home)) != str(codex_home):
        raise ValueError('Codex home differs from the run reservation; restore the recorded installation')
    return RoutedCollector(Path(record['packet']).parent, codex_home, analysis).for_run(run)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('hook', 'status', 'refresh', 'finalize', 'replace', 'supplement', 'adopt'))
    parser.add_argument('run', nargs='?')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--codex-home', type=Path, default=capture.CODEX_HOME)
    parser.add_argument('--transcript-path')
    parser.add_argument('--session-id')
    parser.add_argument('--reason')
    parser.add_argument('--relationship', choices=('continuation', 'new-encounter'))
    parser.add_argument('--allow-incomplete', metavar='REASON')
    args = parser.parse_args()
    if args.command == 'hook':
        try:
            event = json.load(sys.stdin)
            # Registry and records determine the packet; there is no default project.
            from berman.capture.router import route
            result = route(event, args.codex_home, args.root)
        except Exception as error:
            result = {'systemMessage': 'Berman fieldwork capture: ' + str(error)}
        print(json.dumps(result))  # Never block, continue a turn, or inject instructions.
        return
    if not args.run:
        parser.error('run is required for status and recovery commands')
    try:
        print(json.dumps(collector(args.run, args.codex_home, args.root).command(
            args.command, args.run, reason=args.allow_incomplete or args.reason, transcript_path=args.transcript_path,
            relationship=args.relationship, session_id=args.session_id), indent=2))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
