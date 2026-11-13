#!/usr/bin/env python3
"""Prepare, inspect, capture and migrate fieldwork packets without one-off glue."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import time

from berman import files
from berman._legacy import api as legacy

# A notes-layout support packet records this protocol and connection identity; recipes never name them.
SUPPORT_PROTOCOL, SUPPORT_CONNECTION = 'support-trial/1', 'support-trial'


@contextmanager
def workspace_lock(analysis, wait=30):
    """Berman's short global critical section: run allocation and shared inventory updates."""
    fd = os.open(Path(analysis) / '.berman.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError('Another operation holds the Berman workspace allocation and index; retry when it finishes')
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)


def prepare(collector, run, assignment, manager_cwd, revision, coverage, instructions_from=None, recording_protocol=None,
            marker=None, layout='legacy', connection=None, harness='Codex CLI'):
    """The legacy layout keeps narrative.md and scratch/ placeholders for the frozen collector.

    The notes layout omits them, so the frozen collector cannot register such a packet.
    """
    if not legacy.RUN.fullmatch(run):
        raise ValueError('Run must have 3 to 6 digits')
    if not assignment.strip() or not revision.strip():
        raise ValueError('Assignment and revision are required; use an explicit description such as not-applicable for a support trial')
    files.safe_directory(manager_cwd)
    notes_only = set(coverage) == {'notes'}
    if recording_protocol is not None and (not notes_only or recording_protocol not in ('work-accounts/1', 'participant-inquiry/1', SUPPORT_PROTOCOL)):
        raise ValueError('The selected recording protocol requires notes coverage and a supported protocol identity')
    if recording_protocol == SUPPORT_PROTOCOL and connection != SUPPORT_CONNECTION:
        raise ValueError(f'{SUPPORT_PROTOCOL} is reserved for native support-trial packets')
    allowed = {'narrative', 'scratch', 'git-log', 'auditor-streams', 'narrative-timestamps', 'full-command-outputs'}
    if not notes_only and (not {'narrative', 'scratch'} <= set(coverage) or set(coverage) - allowed):
        raise ValueError('Coverage must be notes alone, or include narrative,scratch with optional git-log,auditor-streams,narrative-timestamps,full-command-outputs')
    if notes_only and not instructions_from:
        raise ValueError('Notes coverage requires explicit researcher-authored --instructions-from')
    if layout not in ('legacy', 'notes') or (layout == 'notes' and (not notes_only or not marker or not connection)):
        raise ValueError('The notes layout needs notes coverage, its collector marker, and a connection identity')
    collector.fieldwork.mkdir(parents=True, exist_ok=True)
    files.safe_directory(collector.fieldwork)
    root = collector.fieldwork / run
    with collector.locked():
        if root.exists() or root.is_symlink() or collector.archived(run):
            raise ValueError('Run number or destination already exists; preparation never overwrites a packet')
        source = None
        if instructions_from:
            source = files.regular(Path(instructions_from))
            text = source.read_text()
            # Adapt explicit recording locations without executing the source.
            import re
            text = re.sub(r'(?:~|/)[^`\n]*?/run-[0-9]{3,6}(?=/(?:narrative\.md|scratch(?:/|`)|notes(?:/|`)|git-commands\.log))', lambda _: str(root), text)
            expected = ('notes',) if notes_only else ('narrative.md', 'scratch')
            if any(str(root / name) not in text for name in expected):
                raise ValueError('Could not identify the recording paths in the supplied instructions; supply a source with explicit paths')
            if notes_only and ('narrative.md' in text or str(root / 'scratch') in text):
                raise ValueError('Notes instructions must not carry the old narrative or scratch destinations')
            if 'git-commands.log' in text and 'git-log' not in coverage:
                raise ValueError('Source instructions require a Git log but coverage does not; choose compatible instructions or coverage')
        else:
            text = (f'# Stewart Fieldwork — Recording Instructions\n\nMaintain a running narrative in `{root}/narrative.md`.\n'
                    'In ordinary prose, record what you did, what happened, and what you did next. Update it periodically.\n\n'
                    f'Keep scratch probes, temporary code, and saved outputs under `{root}/scratch/`.\n'
                    'Organize them as convenient.\n')
        additions = {
            'git-log': f'Append direct Git invocations to `{root}/git-commands.log` with recorded times.',
            'auditor-streams': 'Save each available auditor stream separately under scratch/ with a filename beginning auditor and ending .jsonl; subdirectories are supported. Record any unavailable stream.',
            'narrative-timestamps': 'Give narrative entries their recorded UTC time on a separate line in YYYY-MM-DDTHH:MM:SSZ form; do not infer missing times.',
            'full-command-outputs': 'Save full stdout/stderr for commands whose outputs would otherwise be truncated; note anything unavailable.'}
        extra = [additions[key] for key in coverage if key in additions]
        if extra:
            text += '\n## Additional requested coverage\n\n' + '\n\n'.join(extra) + '\n'
        opening = ((marker or legacy.MARKER) + run + '\n\n'
                   f'Your working directory is `{manager_cwd}`. The revision or study target is: {revision}.\n'
                   f'Read `{root}/INSTRUCTIONS.md` before beginning and follow its recording requirements.\n\n'
                   + assignment.rstrip() + '\n')
        metadata = {'schema': 'stewart.run-preparation/1', 'run': run, 'prepared_at_utc': files.now(),
                    'manager': {'cwd': str(manager_cwd), 'revision': revision, 'harness': harness,
                                'model': 'selected by operator; not set by this packet'},
                    'coverage': coverage, 'instructions_from': str(source) if source else None,
                    'source_instructions_sha256': files.digest(source.read_bytes()) if source else None,
                    'assignment_sha256': files.digest(assignment.encode())}
        if notes_only:
            metadata['recording_protocol'] = recording_protocol or 'work-accounts/1'
            metadata['collector_layout'] = {
                'notes/': 'Requested participant accounts and selected supporting copies.',
                'narrative.md': 'Empty collector-layout placeholder; no narrative requested.',
                'scratch/': 'Collector-layout placeholder; no general scratch collection requested.'}
        if layout == 'notes':
            metadata['schema'] = 'berman.run-preparation/1'
            metadata.update(connection=connection, capture_marker=marker.strip())
            metadata['collector_layout'] = {'notes/': 'Requested participant accounts and selected supporting copies.'}
        staging = Path(tempfile.mkdtemp(prefix='.' + run + '-prepare-', dir=collector.fieldwork))
        try:
            if layout == 'legacy':
                (staging / 'scratch').mkdir()
                (staging / 'narrative.md').write_text('')
            if notes_only:
                (staging / 'notes').mkdir()
            (staging / 'INSTRUCTIONS.md').write_text(text)
            (staging / 'ASSIGNMENT.md').write_text(opening)
            (staging / 'run.json').write_bytes(files.json_bytes(metadata))
            if 'git-log' in coverage:
                (staging / 'git-commands.log').write_text('')
            staging.rename(root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {'state': 'prepared', 'path': str(root), 'assignment': str(root / 'ASSIGNMENT.md'),
            'manager_cwd': str(manager_cwd), 'coverage': coverage,
            'next': f'Use ASSIGNMENT.md in the prepared {harness} session; inspect status after its first response'}


def migration_entry(root, run, destination):
    entries = files.inventory(root)
    pointers = [{'path': row['path'], 'content': (root / row['path']).read_text()}
                for row in entries if row['type'] == 'file' and Path(row['path']).name == '.git']
    directories = [row['path'] for row in entries if row['type'] == 'directory' and Path(row['path']).name == '.git']
    st = root.stat()
    return {'run': run, 'original_root': str(root), 'current_root': 'runs/' + run,
            'relocated_at_utc': files.now(), 'root_mode': oct(stat.S_IMODE(st.st_mode)), 'root_mtime_ns': st.st_mtime_ns,
            'entries': entries, 'git_pointers': pointers, 'git_directories': directories,
            'external_git_policy': 'preserve all recorded bytes; no external Git administration changed'}


def verify_moved(root, entry):
    st = root.stat()
    if files.inventory(root) != entry['entries'] or oct(stat.S_IMODE(st.st_mode)) != entry['root_mode'] or st.st_mtime_ns != entry['root_mtime_ns']:
        raise ValueError('Migration inventory differs, including modes or mtimes; both journal and packet were retained for investigation')


def migrate(collector, run, plan=False):
    """Inventory without the callback lock; hold it only to recheck metadata and rename.

    Live callbacks never write beneath runs/, so full byte verification after the move needs no
    callback lock. Shared inventory updates use Berman's short workspace critical section.
    """
    if not legacy.RUN.fullmatch(run):
        raise ValueError('Invalid run number')
    files.safe_directory(collector.analysis)
    source = collector.fieldwork / run
    destination = collector.analysis / 'runs' / run
    layout_path = collector.analysis / 'source-layout.json'
    journal_dir = collector.analysis / 'migrations'
    journal_path = journal_dir / (run + '.json')
    journal = files.read_json(journal_path) if journal_path.exists() else None
    if journal:
        if journal['source'] != str(source) or journal['destination'] != str(destination):
            raise ValueError('Migration journal belongs to different roots')
        entry = journal['entry']
    else:
        layout = files.read_json(layout_path)
        if any(r['run'] == run for r in layout['runs']) or destination.exists() or destination.is_symlink():
            raise ValueError('Migration destination or indexed run already exists; nothing moved')
        collector.run_path(run)
        status = collector.status(run)
        if not status['finalized'] or status.get('snapshot_consistent') is not True:
            raise ValueError('Migrate requires a finalized, consistent manager capture')
        destination.parent.mkdir(parents=True, exist_ok=True)
        files.safe_directory(destination.parent)
        if source.stat().st_dev != destination.parent.stat().st_dev:
            raise ValueError('Cross-filesystem migration is refused; no copy or deletion was attempted')
        entry = migration_entry(source, run, destination)
        journal = {'schema': 'stewart.migration/1', 'source': str(source), 'destination': str(destination),
                   'phase': 'inventoried', 'entry': entry}
    if plan:
        return {'run': run, 'source': str(source), 'destination': str(destination), 'phase': journal['phase'],
                'entries': len(entry['entries']), 'git_pointers': entry['git_pointers'],
                'external_git_policy': entry['external_git_policy'], 'operation': 'same-filesystem rename, verify, then index'}
    journal_dir.mkdir(exist_ok=True)
    files.safe_directory(journal_dir)
    with collector.locked():
        if source.exists():
            if destination.exists() or destination.is_symlink():
                raise ValueError('Both migration paths exist; nothing overwritten')
            if not files.metadata_matches(source, entry['entries'], entry['root_mode'], entry['root_mtime_ns']):
                raise ValueError('Packet changed after its migration inventory; nothing moved. Retry when the engagement is idle'
                                 if not journal_path.exists() else
                                 'Migration inventory differs, including modes or mtimes; both journal and packet were retained for investigation')
            if source.stat().st_dev != destination.parent.stat().st_dev:
                raise ValueError('Cross-filesystem migration is refused')
            if not journal_path.exists():
                files.atomic(journal_path, files.json_bytes(journal))
            os.rename(source, destination)
    if not destination.is_dir():
        raise ValueError('Neither migration path exists; inspect the retained journal')
    verify_moved(destination, entry)
    journal['phase'] = 'moved-and-verified'
    files.atomic(journal_path, files.json_bytes(journal))
    with workspace_lock(collector.analysis):
        layout = files.read_json(layout_path)
        existing = next((r for r in layout['runs'] if r['run'] == run), None)
        if existing and existing != entry:
            raise ValueError('Run is already indexed with a different inventory')
        if not existing:
            layout['runs'].append(entry)
            files.atomic(layout_path, files.json_bytes(layout))
    journal['phase'] = 'indexed'
    files.atomic(journal_path, files.json_bytes(journal))
    return {'run': run, 'state': 'migrated', 'path': str(destination), 'journal': str(journal_path),
            'external_git_policy': entry['external_git_policy'],
            'next': 'Build and verify the desired archive edition; Git commit is a separate action'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'status', 'refresh', 'finalize', 'replace', 'supplement', 'adopt', 'migrate', 'doctor'))
    parser.add_argument('run', nargs='?')
    parser.add_argument('--fieldwork-root', type=Path, required=True)
    parser.add_argument('--analysis-root', type=Path, required=True)
    parser.add_argument('--codex-home', type=Path, default=legacy.CODEX_HOME)
    parser.add_argument('--assignment-file', type=Path)
    parser.add_argument('--manager-cwd', type=Path)
    parser.add_argument('--revision')
    parser.add_argument('--coverage', help='notes with explicit instructions, or narrative,scratch and optional coverage')
    parser.add_argument('--instructions-from', type=Path)
    parser.add_argument('--recording-protocol', choices=('work-accounts/1', 'participant-inquiry/1'))
    parser.add_argument('--reason')
    parser.add_argument('--relationship', choices=('continuation', 'new-encounter'))
    parser.add_argument('--allow-incomplete', metavar='REASON')
    parser.add_argument('--transcript-path')
    parser.add_argument('--session-id')
    parser.add_argument('--plan', action='store_true', help='Inspect migration without moving or indexing')
    args = parser.parse_args()
    from berman.capture.runtime import LegacyCollector
    collector = LegacyCollector(args.fieldwork_root, args.codex_home, args.analysis_root)
    if args.run:
        collector = collector.for_run(args.run)
    try:
        if args.run and (args.analysis_root / 'operations' / args.run / 'operation.json').exists():
            from berman.records import Store
            store = Store(args.analysis_root)
            collector = store.collector_for(store.record(args.run))
        if args.command == 'doctor':
            from berman.records import Store
            from berman.capture.adapters import readiness
            store = Store(args.analysis_root)
            result = {adapter: readiness(store, adapter) for adapter in store.collectors}
        elif not args.run:
            parser.error('run is required')
        elif args.command == 'prepare':
            if not all((args.assignment_file, args.manager_cwd, args.revision, args.coverage)):
                parser.error('prepare requires --assignment-file, --manager-cwd, --revision and --coverage')
            result = prepare(collector, args.run, files.regular(args.assignment_file).read_text(),
                             args.manager_cwd.expanduser().absolute(), args.revision,
                             [item.strip() for item in args.coverage.split(',')], args.instructions_from, args.recording_protocol)
        elif args.command == 'migrate':
            result = migrate(collector, args.run, args.plan)
        else:
            result = collector.command(args.command, args.run, reason=args.allow_incomplete or args.reason,
                                       transcript_path=args.transcript_path, relationship=args.relationship, session_id=args.session_id)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
