"""A co-located workspace as release 0.26.3 left it, built from synthetic records.

No product code prepares, captures or preserves a co-located run any more; this version only
reads one. These builders write the records that release's writer wrote, so the readers stay
exercised: a prepared packet with its operation record, a registered session with committed
capture generations, a frozen capture, a declared ending, the migration inventory and journal, a
recording/2 manifest with its package, and the closeout receipt with its reading edition.

They are fixtures, not a second lifecycle. They take no lock, run no harness, make no decision a
researcher made, and are never installed. Each step leaves the workspace in a state the reader has
to describe, including the unsettled ones a study could have stopped in.

Each record holds the fields the readers use and no field the release did not write; a real record
holds more (installation digests, launch settings, closing context). When these builders were
written, a preserved run and an open engagement made by them were compared with the same two made
by the 0.26.3 writer itself: the readers reported the same status for both.
"""

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from berman import evidence, files, recipes, recordings, records, snapshots
from berman._legacy.api import RUN
from berman.archive import builder
from berman.archive.select import select
from berman.capture import collector

MARKER = 'Berman-Fieldwork-Run: '
RELEASE = '0.26.3'
ENDING_MEANING = 'Operator declares this engagement ended; not a claim that its development or inquiry is complete.'


def next_run(store):
    """The next research identity, as the writer's allocation would have chosen it."""
    bases = (store.field, store.root / 'runs', store.operations)
    numbers = [int(p.name[4:]) for base in bases if base.exists() for p in base.iterdir() if RUN.fullmatch(p.name)]
    return f'run-{max([*numbers, *(int(r[4:]) for r in store.historical_runs)], default=0) + 1:03d}'


def save(store, record):
    """Write an operation record the reader accepts; an invalid fixture fails here, not in the test."""
    files.write(store.folder(record['run']) / 'operation.json', records.checked(record, record['run']))
    return record


def prepare(store, connection=None, recipe=None, run=None):
    """A prepared, unstarted packet and its `ready` record. Without a recipe, the selected plan's next step."""
    conn = store.connections[connection or store.default_connection]
    run = run or next_run(store)
    step = None
    if recipe is None:
        step = conn.plan.next_step(store.discover())
        recipe = step['recipe']
    selected = recipes.recipe(store, recipe, conn)
    packet, folder = store.field / run, store.folder(run)
    recipes.snapshot(conn.checkout, conn.definition.get('documents', []), folder / 'preparation/initial-project')
    (packet / 'notes').mkdir(parents=True)
    packet.chmod(0o700)
    (packet / 'INSTRUCTIONS.md').write_text(
        selected['instructions'].replace('{{notes_directory}}', str(packet / 'notes') + '/')
    )
    (packet / 'ASSIGNMENT.md').write_text(f'{MARKER}{run}\n\nYour working directory is `{conn.checkout}`.\n')
    protocol = selected['metadata']['recording_protocol']
    files.write(
        packet / 'run.json',
        {
            'schema': 'berman.run-preparation/1',
            'run': run,
            'prepared_at_utc': files.now(),
            'manager': {'cwd': str(conn.checkout), 'project': conn.name, 'harness': 'Codex CLI'},
            'coverage': ['notes'],
            'recording_protocol': protocol,
            'connection': conn.id,
            'capture_marker': MARKER.strip(),
        },
    )
    for name in ('INSTRUCTIONS.md', 'ASSIGNMENT.md', 'run.json'):
        files.keep(folder / 'preparation' / name, (packet / name).read_bytes())
    record = {
        'schema': 'berman.operation/1',
        'run': run,
        'phase': 'ready',
        'software': {'schema': 'berman.software/1', 'version': RELEASE},
        'reserved_at_utc': files.now(),
        'workspace': {'root': str(store.root)},
        'connection': {
            'id': conn.id,
            'name': conn.name,
            'definition': str(conn.definition_path.relative_to(store.root)),
            'documents': list(conn.definition.get('documents', [])),
            'checks': list(conn.definition.get('checks', [])),
            'readers': list(conn.definition.get('readers', [])),
            'checkout': str(conn.checkout),
        },
        'recipe': selected,
        'assignment_origin': 'plan' if step else 'direct',
        'closeout_contract': 'reading/1',
        'recording_protocol': protocol,
        'project': str(conn.checkout),
        'documents': list(conn.definition.get('documents', [])),
        'packet_root': str(store.field),
        'packet': str(packet),
        'capture': {'adapter': 'berman-codex', 'harness': 'codex', 'marker': MARKER, 'layout': 'notes'},
        'harness': {'id': 'codex', 'home': str(store.home)},
        'retained_open_runs': [],
    }
    if step:
        record.update(plan=conn.plan.reservation(step), step=step['id'])
    save(store, record)
    return run


def transcript(store, sid, reply='done'):
    """A native Codex session source in the workspace's Codex home."""
    source = store.home / 'sessions' / (sid + '.jsonl')
    source.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {'type': 'session_meta', 'payload': {'id': sid}},
        {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'text': reply}]}},
    ]
    source.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return source


def registration(store, run, sid, **state):
    """The Codex adapter's session registry entry."""
    path = store.registries[0] / (sid + '.json')
    previous = files.read(path) if path.exists() else {}
    value = {
        'run': run,
        'session_id': sid,
        'active_turn': True,
        'workspace': str(store.root),
        'harness': 'codex',
        'collector': 'berman-collector/1',
        'packet': str(store.field / run),
        'adapter': 'berman-codex',
    }
    files.write(path, {**value, **previous, **state})


def capture(store, run, event):
    """Commit one capture generation of the bound session's source, as a callback or finalization did."""
    session = store.field / run / 'session'
    binding = files.read(session / 'binding.json')
    data = Path(binding['transcript_path']).read_bytes()
    end = data.rfind(b'\n') + 1
    raw, tail = data[:end], data[end:]
    reader, _, malformed = collector.capture.render(raw)
    previous = snapshots.read(session)
    meta = {
        'schema': 'berman.session-capture/2',
        'harness': 'codex',
        'format': 'codex-jsonl/1',
        'run': run,
        'session_id': binding['session_id'],
        'captured_at_utc': files.now(),
        'event': event,
        'turn_id': None if event.startswith('manual-') else 'one',
        'source_transcript': binding['transcript_path'],
        'source_size_at_read': len(data),
        'saved_bytes': len(raw),
        'omitted_partial_tail_bytes': len(tail),
        'sha256': files.digest(raw),
        'partial_tail_file': 'partial-tail-' + files.digest(tail) + '.bin' if tail else None,
        'partial_tail_sha256': files.digest(tail) if tail else None,
        'source_identity': 'session_meta ID verified',
        'previous_snapshot_is_prefix': raw.startswith(previous['raw']) if previous else None,
        'last_assistant_message_present': None,
        'expected_last_assistant_sha256': None,
        'unparsed_jsonl_lines': malformed,
        'reader_sha256': files.digest(reader),
        'scope': 'Persisted full session at capture time; no assertion of research completion.',
    }
    return snapshots.publish(session, meta, raw, reader, tail, store.root / '.state/capture-staging' / run)


def start(store, run, stop=True, end=False, sid=None):
    """Bind a session to a prepared packet, then optionally commit its Stop and SessionEnd captures."""
    sid = sid or 'fixture-' + run
    packet = store.field / run
    source = transcript(store, sid)
    (packet / 'session').mkdir(mode=0o700)
    files.write(
        packet / 'session/binding.json',
        {
            'schema': 'stewart.session-binding/1',
            'run': run,
            'session_id': sid,
            'registered_at_utc': files.now(),
            'start_turn_id': 'one',
            'run_directory_at_registration': str(packet),
            'transcript_path': str(source),
            'marker': MARKER + run,
            'collector': 'berman-collector/1',
            'workspace': str(store.root),
        },
    )
    registration(store, run, sid)
    for event in (['Stop'] if stop else []) + (['SessionEnd'] if end else []):
        capture(store, run, event)
        registration(store, run, sid, active_turn=False, last_terminal_event=event)
    return sid


def finalize(store, run):
    """Freeze the capture. This closes a capture and never ends an engagement."""
    session = store.field / run / 'session'
    meta = capture(store, run, 'manual-finalize')
    files.write(
        session / 'finalized.json',
        {
            'schema': 'berman.session-finalization/3',
            'run': run,
            'session_id': meta['session_id'],
            'finalized_at_utc': files.now(),
            'capture_sha256': meta['sha256'],
            'saved_bytes': meta['saved_bytes'],
            'generation': snapshots.pointer(session)['generation'],
            'boundary': {'ended': False, 'warnings': [], 'override_reason': None},
            'meaning': 'Snapshot frozen for preservation; does not assert scientific completion.',
        },
    )


def declare(store, run, reason, allow_incomplete=None):
    """Record the researcher's ending declaration and leave the engagement `closing`: nothing preserved yet."""
    record = store.record(run)
    binding = files.read(store.field / run / 'session/binding.json')
    record.update(
        phase='closing',
        started_session=binding['session_id'],
        ending={
            'declared_at_utc': files.now(),
            'reason': reason,
            'allow_incomplete': allow_incomplete,
            'meaning': ENDING_MEANING,
        },
    )
    return save(store, record)


def migration_entry(root, run, original=None):
    """The inventory a migration recorded for a packet, taken where the packet now is."""
    state = root.stat()
    return {
        'run': run,
        'original_root': str(original or root),
        'current_root': 'runs/' + run,
        'relocated_at_utc': files.now(),
        'root_mode': oct(stat.S_IMODE(state.st_mode)),
        'root_mtime_ns': state.st_mtime_ns,
        'entries': files.inventory(root),
        'git_pointers': [],
        'git_directories': [],
        'external_git_policy': 'preserve all recorded bytes; no external Git administration changed',
    }


def migrate(store, run, phase='indexed'):
    """Inventory the packet, journal the move and rename it under runs/; index it unless stopped earlier."""
    source, destination = store.field / run, store.root / 'runs' / run
    entry = migration_entry(source, run)
    journal = {
        'schema': 'stewart.migration/1',
        'source': str(source),
        'destination': str(destination),
        'phase': phase,
        'entry': entry,
    }
    files.write(store.root / 'migrations' / (run + '.json'), journal)
    destination.parent.mkdir(exist_ok=True)
    os.rename(source, destination)
    if phase == 'indexed':
        layout = files.read(store.root / 'source-layout.json')
        layout['runs'].append(entry)
        files.write(store.root / 'source-layout.json', layout)
    return entry


def package(store, run):
    """Retain the recording/2 manifest and authority beside the originals and publish its package."""
    folder = recordings.manifest_path(store.root, run).parent
    folder.parent.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.' + run + '-', dir=folder.parent))
    try:
        manifest = recordings.build_manifest(store, run, stage)
        stage.rename(folder)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    payload = recordings.manifest_members(store.root, manifest)
    data = builder.make_zip(payload, '')
    destination = store.root / 'archive/recordings' / (run + '.recording-v2.zip')
    files.keep(destination, data)
    files.keep(destination.with_suffix('.zip.sha256'), f'{files.digest(data)}  {destination.name}\n'.encode())
    publication = {
        'relative_path': destination.relative_to(store.root).as_posix(),
        'schema': evidence.SCHEMA,
        'sha256': files.digest(data),
        'bytes': len(data),
        'members': len(payload),
        'namespace': '',
        'manifest': (folder / 'manifest.json').relative_to(store.root).as_posix(),
        'manifest_sha256': evidence.identity(manifest),
        'coverage': manifest['coverage'],
    }
    files.keep(folder / 'publication.json', files.json_bytes(publication))
    return dict(publication, path=str(destination))


def finish(store, run, reason='Explicit synthetic ending', reading=True):
    """A complete closeout: declared ending, frozen capture, migration, recording package and reading edition."""
    record = declare(store, run, reason)
    packet = store.field / run
    if not (packet / 'session/finalized.json').exists():
        finalize(store, run)
    context = packet / 'context/berman'
    recipes.snapshot(Path(record['project']), record['documents'], context / 'closing-project')
    shutil.copytree(store.folder(run) / 'preparation', context / 'preparation')
    files.write(context / 'ending.json', record['ending'])
    migrate(store, run)
    record['package'] = package(store, run)
    record['phase'] = 'archived'
    save(store, record)
    result = {'run': run, 'state': 'archived', 'package': record['package']}
    if reading:
        result['reading'] = builder.edition(select(store.root, 'connections', runs=[run]))
        files.write(
            store.folder(run) / 'closeout.json',
            {'schema': 'berman.closeout/1', 'run': run, 'reading': result['reading'], 'completed_at_utc': files.now()},
        )
    return result


def support_trial(store, run='run-900101', evidence='experiments/trial-a', phase='launching', adapter='berman-codex'):
    """A native support trial's immutable marker and, unless phase is None, its changing receipt."""
    save(
        store,
        {
            'schema': 'berman.operation/1',
            'run': run,
            'phase': 'support-trial',
            'purpose': 'support-trial',
            'software': {'schema': 'berman.software/1', 'version': RELEASE},
            'support_trial': evidence + '/trial.json',
            'workspace': {'root': str(store.root)},
            'packet_root': str(store.field),
            'packet': str(store.field / run),
            'capture': {'adapter': adapter},
            'harness': {'id': 'codex', 'home': str(store.home)},
        },
    )
    if phase is not None:
        files.write(
            store.root / evidence / 'trial.json',
            {'schema': 'berman.support-trial/1', 'evidence': evidence, 'run': run, 'adapter': adapter, 'phase': phase},
        )
    return run
