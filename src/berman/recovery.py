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
import tempfile
import time

from berman import files
from berman import schemas
from berman._legacy import api as legacy

# A notes-layout support packet records this protocol and connection identity; recipes never name them.
SUPPORT_PROTOCOL, SUPPORT_CONNECTION = 'support-trial/1', 'support-trial'


def quarantine(analysis, run, paths, reason):
    """Retain abandoned writer-owned material outside packets before any inventory exists.

    Callers select only Berman-owned temporary namespaces and hold the appropriate operation
    or capture lock. A durable intent precedes the same-filesystem rename; neither a crash
    nor a retry deletes the source. The material's presence beside the receipt establishes
    whether the move completed. Never use this on an inventoried or preserved tree.
    """
    retained = []
    for source in sorted(set(paths)):
        if not source.exists() and not source.is_symlink():
            continue
        journal = analysis / 'migrations' / (run + '.json')
        if journal.exists() or journal.is_symlink():
            raise files.Decision(f'Recovery never cleans material after migration inventory: {journal}')
        if source.is_relative_to(analysis / 'runs'):
            raise files.Decision('Recovery never moves material from preserved runs')
        if source.is_dir():
            entries = files.inventory(source)
            content = {'kind': 'directory', 'entries': entries}
        else:
            data = files.regular(source).read_bytes()
            content = {'kind': 'file', 'bytes': len(data), 'sha256': files.digest(data)}
        state = source.stat()
        receipt = {'schema': 'berman.recovered-write/1', 'run': run, 'source': str(source),
                   'reason': reason, 'mode': oct(stat.S_IMODE(state.st_mode)), 'mtime_ns': state.st_mtime_ns,
                   'material': 'material', **content}
        schemas.validate('berman.recovered-write/1', receipt)
        directory = analysis / '.state/recovery' / run / files.digest_json(receipt)
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise files.Decision(f'Symlink in recovery destination: {directory}')
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / 'material'
        if target.exists() or target.is_symlink():
            raise files.Decision(f'Both recovery source and retained material exist: {source}; {target}')
        files.keep(directory / 'receipt.json', files.json_bytes(receipt))
        os.rename(source, target)
        verify_recovered(target, receipt)
        retained.append(str(directory / 'receipt.json'))
    return retained


def verify_recovered(target, receipt):
    """Verify retained bytes and metadata; a damaged recovery copy needs attention too."""
    if receipt['kind'] == 'file':
        data = files.regular(target).read_bytes()
        matches = len(data) == receipt['bytes'] and files.digest(data) == receipt['sha256']
    else:
        matches = files.inventory(target) == receipt['entries']
    state = target.stat()
    if not matches or oct(stat.S_IMODE(state.st_mode)) != receipt['mode'] or state.st_mtime_ns != receipt['mtime_ns']:
        raise files.Decision(f'Recovered material differs from its receipt: {target}')


def generated_residue(store, run):
    """Only operation-owned atomic writes and unpublished snapshot/packet staging directories.

    Published project snapshots can contain arbitrarily named project files, including names
    beginning .capture-. They are never swept. Notes and other participant files are not visited.
    """
    folder = store.folder(run)
    preparation = folder / 'preparation'
    paths = list(folder.glob('.capture-*')) + list(folder.glob('.snapshot-*'))
    paths += list(preparation.glob('.snapshot-*'))
    for path in preparation.rglob('.capture-*'):
        first = path.relative_to(preparation).parts[0]
        if first != 'initial-project' and not first.startswith('.snapshot-'):
            paths.append(path)
    paths += list(store.field.glob('.' + run + '-prepare-*'))
    quarantine(store.root, run, paths, 'Abandoned unpublished preparation or operation write')
    return recovered_writes(store.root, run)


def recovered_writes(analysis, run):
    """Completed recovery moves, including a prior process killed just after the rename."""
    result = []
    for path in sorted((analysis / '.state/recovery' / run).glob('*/receipt.json')):
        receipt = schemas.validate('berman.recovered-write/1', files.read(path), path)
        if (path.parent / receipt['material']).exists():
            verify_recovered(path.parent / receipt['material'], receipt)
            result.append(str(path))
        elif not Path(receipt['source']).exists():
            raise files.Decision(f'Recovery source and retained material are both missing: {path}')
    return result


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
                    raise ValueError('Another operation holds the Berman workspace allocation and index; retry '
                                     'when it finishes') from None
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)


def prepare(collector, run, assignment, manager_cwd, revision, coverage, instructions_from=None, recording_protocol=None,
            marker=None, layout='notes', connection=None, harness='Codex CLI'):
    """Prepare a notes packet: INSTRUCTIONS.md, ASSIGNMENT.md, run.json and an empty notes/ directory.

    Packets of the removed legacy-codex adapter also carried narrative.md and scratch/ placeholders;
    those are no longer prepared.
    """
    if not legacy.RUN.fullmatch(run):
        raise ValueError('Run must have 3 to 6 digits')
    if not assignment.strip() or not revision.strip():
        raise ValueError('Assignment and revision are required; use an explicit description such as not-applicable for a support trial')
    files.safe_directory(manager_cwd)
    if layout != 'notes' or set(coverage) != {'notes'}:
        raise ValueError('Packets use the notes layout with notes coverage; the narrative and scratch layout was removed')
    if recording_protocol is not None and recording_protocol not in ('work-accounts/1', 'participant-inquiry/1', SUPPORT_PROTOCOL):
        raise ValueError('The selected recording protocol requires a supported protocol identity')
    if recording_protocol == SUPPORT_PROTOCOL and connection != SUPPORT_CONNECTION:
        raise ValueError(f'{SUPPORT_PROTOCOL} is reserved for native support-trial packets')
    if not instructions_from:
        raise ValueError('Notes coverage requires explicit researcher-authored --instructions-from')
    if not marker or not connection:
        raise ValueError('The notes layout needs its collector marker and a connection identity')
    collector.fieldwork.mkdir(parents=True, exist_ok=True)
    files.safe_directory(collector.fieldwork)
    root = collector.fieldwork / run
    with collector.locked():
        if root.exists() or root.is_symlink() or collector.archived(run):
            raise ValueError('Run number or destination already exists; preparation never overwrites a packet')
        source = files.regular(Path(instructions_from))
        text = source.read_text()
        # Adapt explicit recording locations without executing the source.
        import re
        text = re.sub(r'(?:~|/)[^`\n]*?/run-[0-9]{3,6}(?=/(?:narrative\.md|scratch(?:/|`)|notes(?:/|`)|git-commands\.log))', lambda _: str(root), text)
        if str(root / 'notes') not in text:
            raise ValueError('Could not identify the recording paths in the supplied instructions; supply a source with explicit paths')
        if 'narrative.md' in text or str(root / 'scratch') in text:
            raise ValueError('Notes instructions must not carry the old narrative or scratch destinations')
        if 'git-commands.log' in text:
            raise ValueError('Source instructions require a Git log, which notes coverage does not provide')
        opening = (marker + run + '\n\n'
                   f'Your working directory is `{manager_cwd}`. The revision or study target is: {revision}.\n'
                   f'Read `{root}/INSTRUCTIONS.md` before beginning and follow its recording requirements.\n\n'
                   + assignment.rstrip() + '\n')
        metadata = {'schema': 'berman.run-preparation/1', 'run': run, 'prepared_at_utc': files.now(),
                    'manager': {'cwd': str(manager_cwd), 'revision': revision, 'harness': harness,
                                'model': 'selected by operator; not set by this packet'},
                    'coverage': coverage, 'instructions_from': str(source),
                    'source_instructions_sha256': files.digest(source.read_bytes()),
                    'assignment_sha256': files.digest(assignment.encode()),
                    'recording_protocol': recording_protocol or 'work-accounts/1',
                    'collector_layout': {'notes/': 'Requested participant accounts and selected supporting copies.'},
                    'connection': connection, 'capture_marker': marker.strip()}
        staging = Path(tempfile.mkdtemp(prefix='.' + run + '-prepare-', dir=collector.fieldwork))
        try:
            (staging / 'notes').mkdir()
            (staging / 'INSTRUCTIONS.md').write_text(text)
            (staging / 'ASSIGNMENT.md').write_text(opening)
            (staging / 'run.json').write_bytes(files.json_bytes(metadata))
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
        recovered = []
        if not plan:
            with collector.locked():
                paths = [path for name in ('session', 'session-history', 'session-supplements')
                         for path in (source / name).rglob('.capture-*')]
                recovered = quarantine(collector.analysis, run, paths, 'Abandoned capture write before migration')
        entry = migration_entry(source, run, destination)
        journal = {'schema': 'stewart.migration/1', 'source': str(source), 'destination': str(destination),
                   'phase': 'inventoried', 'entry': entry}
        if recovered:
            journal['recovered_writes'] = recovered
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
    parser.add_argument('command', choices=('status', 'refresh', 'finalize', 'replace', 'supplement', 'adopt', 'migrate', 'doctor'))
    parser.add_argument('run', nargs='?')
    parser.add_argument('--fieldwork-root', type=Path, required=True)
    parser.add_argument('--analysis-root', type=Path, required=True)
    parser.add_argument('--codex-home', type=Path, default=legacy.CODEX_HOME)
    parser.add_argument('--reason')
    parser.add_argument('--relationship', choices=('continuation', 'new-encounter'))
    parser.add_argument('--allow-incomplete', metavar='REASON')
    parser.add_argument('--transcript-path')
    parser.add_argument('--session-id')
    parser.add_argument('--plan', action='store_true', help='Inspect migration without moving or indexing')
    args = parser.parse_args()
    try:
        from berman.records import Store
        store = Store(args.analysis_root)
        if args.command == 'doctor':
            from berman.capture.adapters import readiness
            result = {adapter: readiness(store, adapter) for adapter in store.collectors}
        elif not args.run:
            parser.error('run is required')
        else:
            # A run is operated on only through its operation record, which names its adapter and installation.
            record = store.record(args.run)
            if record is None:
                raise ValueError(f'{args.run} has no operation record. Packets are prepared by berman new; a preserved '
                                 'run without a record is read in place beneath runs/')
            collector = store.collector_for(record)
            if args.command == 'migrate':
                result = migrate(collector, args.run, args.plan)
            else:
                result = collector.command(args.command, args.run, reason=args.allow_incomplete or args.reason,
                                           transcript_path=args.transcript_path, relationship=args.relationship, session_id=args.session_id)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
