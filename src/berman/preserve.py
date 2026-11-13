"""Preserve explicitly ended engagements and resume interrupted custody operations."""
import json
import os
from pathlib import Path
import shlex
import stat

import berman_files as files
from berman_files import Decision, keep, read, write
import build_archive
import fieldwork
from berman_lifecycle import declaration, declared
from berman_recipes import snapshot


def copy_tree(source, target):
    """Copy a selected tree once; verify every retained byte on retries."""
    if not source.exists():
        return
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise Decision(f'Unexpected symlink in preparation material: {path}')
        if path.is_file():
            keep(target / path.relative_to(source), path.read_bytes())


def namespace(store, record):
    """Stewart-compatible packages keep their namespace; other connections use Berman's."""
    connection = ((record or {}).get('connection') or {}).get('id')
    if connection is None or connection == store.legacy_connection:
        return build_archive.PREFIX
    return 'berman-analysis/'


def package(store, run):
    layout = read(store.root / 'source-layout.json')
    entry = next(e for e in layout['runs'] if e['run'] == run)
    root = store.root / 'runs' / run
    fieldwork.verify_moved(root, entry)
    payload = {}
    # Existing archive codec preserves modes, symlinks, directory entries and bytes.
    for item in entry['entries']:
        name = 'runs/' + run + '/' + item['path']
        mode = int(item['mode'], 0)
        if item['type'] == 'directory':
            payload[name + '/'] = (b'', stat.S_IFDIR | mode)
        elif item['type'] == 'symlink':
            payload[name] = (os.fsencode(item['target']), stat.S_IFLNK | mode)
        else:
            data = (root / item['path']).read_bytes()
            if files.digest(data) != item['sha256']:
                raise Decision(f'{run}: changed during packaging: {name}')
            payload[name] = (data, stat.S_IFREG | mode)
    payload['recording-inventory.json'] = (files.json_bytes(entry), stat.S_IFREG | 0o644)
    payload['RECORDING.txt'] = (b'Berman recording package. Contains the preserved packet and its migration inventory.\n'
                              b'Full reading editions are built separately with tools/build_archive.py.\n', stat.S_IFREG | 0o644)
    prefix = namespace(store, store.record(run))
    data = build_archive.make_zip(payload, prefix)
    build_archive.verify_zip(data, payload, prefix)
    destination = store.root / 'archive/recordings' / (run + '.zip')
    keep(destination, data)
    # Verify disk bytes, not just the in-memory construction.
    build_archive.verify_zip(destination.read_bytes(), payload, prefix)
    fieldwork.verify_moved(root, entry)
    sha = files.digest(data)
    keep(destination.with_suffix('.zip.sha256'), f'{sha}  {destination.name}\n'.encode())
    return {'path': str(destination), 'sha256': sha, 'bytes': len(data), 'members': len(payload), 'namespace': prefix}


def finish_help(record):
    return (f'berman finish --run {record["run"]} --reason {shlex.quote(record["ending"]["reason"])} '
            '--allow-incomplete "explanation of the intentional boundary"')


def retained_source(store, record, create=True):
    """Recognize frozen bytes or a verified supplement; never rewrite the frozen capture."""
    run = record['run']
    root = store.field / run
    frozen = read(root / 'session/finalized.json')['capture_sha256']
    binding = read(root / 'session/binding.json')
    try:
        source = store.collector_for(record).find_source(binding).read_bytes()
    except FileNotFoundError:
        return {'source_unavailable': True, 'frozen_sha256': frozen}
    source_hash = files.digest(source)
    if source_hash == frozen:
        return {'source_sha256': source_hash, 'retained_in': 'session/transcript.jsonl'}
    for receipt_path in sorted((root / 'session-supplements').glob('*/supplement.json')):
        receipt = read(receipt_path)
        if receipt['frozen_sha256'] != frozen:
            raise Decision(f'{run}: supplement refers to a different frozen capture')
        folder = receipt_path.parent / 'session'
        raw = files.regular(folder / 'transcript.jsonl').read_bytes()
        meta = read(folder / 'capture.json')
        associated = read(folder / 'binding.json')
        history = files.regular(folder / 'captures.jsonl').read_bytes().splitlines()
        consistent = (files.digest(raw) == receipt['supplement_sha256'] == meta['sha256']
                      and len(raw) == meta['saved_bytes'] and associated['session_id'] == binding['session_id']
                      and meta['session_id'] == binding['session_id'] and bool(history)
                      and json.loads(history[-1]) == meta
                      and files.digest(files.regular(folder / 'conversation.txt').read_bytes()) == meta['reader_sha256'])
        if not consistent:
            raise Decision(f'{run}: inconsistent supplement retained for inspection: {receipt_path.parent}')
        tail = b''
        if meta.get('partial_tail_file'):
            tail = files.regular(folder / meta['partial_tail_file']).read_bytes()
            if files.digest(tail) != meta['partial_tail_sha256'] or len(tail) != meta['omitted_partial_tail_bytes']:
                raise Decision(f'{run}: supplement partial tail differs from its capture metadata')
        if files.digest(raw + tail) == source_hash:
            if (tail or meta.get('unparsed_jsonl_lines')) and not record['ending'].get('allow_incomplete'):
                raise Decision(f'{run}: supplement retains incomplete records. For an intentional boundary, use {finish_help(record)}')
            return {'source_sha256': source_hash, 'retained_in': str(receipt_path.parent.relative_to(root)),
                    'partial_tail_bytes': len(tail), 'unparsed_jsonl_lines': meta.get('unparsed_jsonl_lines', [])}
    if create:
        store.collector_for(record).command('supplement', run, reason='Preserve later persisted activity at the declared ending: ' + record['ending']['reason'])
        return retained_source(store, record, create=False)
    raise Decision(f'{run}: source changed during collection. Its earlier snapshots remain retained; retry berman finish --reason '
                   f'{shlex.quote(record["ending"]["reason"])} when this engagement is idle')


def preserve(store, record):
    run = record['run']
    if not declared(record) and not record.get('migration_authority'):
        raise Decision(f'{run}: no declared engagement ending')
    folder = store.folder(run)
    collector = store.collector_for(record)
    if (store.field / run).exists():
        if (store.root / 'migrations' / (run + '.json')).exists():
            # An inventory already exists: never add material after that boundary.
            fieldwork.migrate(collector, run)
        else:
            try:
                collector.command('finalize', run, reason=record['ending'].get('allow_incomplete'))
            except ValueError as exc:
                if str(exc).startswith('Not finalized:'):
                    detail = str(exc).split('. Wait/refresh,')[0]
                    raise Decision(detail + '. Wait for the turn to end, or for an intentional incomplete boundary use ' + finish_help(record)) from exc
                raise
            retained_source(store, record)
            closing = folder / 'closing-project'
            snapshot(Path(record['project']), record['documents'], closing)
            destination = store.field / run / 'context/berman'
            copy_tree(closing, destination / 'closing-project')
            preparation = folder / 'preparation'
            if not preparation.exists() and not record.get('connection'):
                # Only runs predating operation records kept preparation evidence in inquiry/.
                candidates = list((store.root / 'inquiry').glob('*-' + run + '-preparation'))
                if len(candidates) > 1:
                    raise Decision(f'{run}: several historical preparation records need selection')
                preparation = candidates[0] if candidates else preparation
            copy_tree(preparation, destination / 'preparation')
            keep(destination / 'COLLECTION.txt',
                 b'Berman collected selected project documents after the declared engagement ending.\n'
                 b'Timestamps and Git state describe collection time. Missing handoffs or documents\n'
                 b'are recorded as missing; no handoff or successful outcome is inferred.\n')
            # Recognize already supplied supplements or collect growth during context reads.
            coverage = retained_source(store, record)
            write(destination / 'source-coverage.json', coverage)
            ending_path = destination / 'ending.json'
            ending_bytes = files.json_bytes(record['ending'])
            if ending_path.exists() and ending_path.read_bytes() != ending_bytes:
                old = files.regular(ending_path).read_bytes()
                keep(destination / 'ending-history' / (files.digest(old) + '.json'), old)
            write(ending_path, record['ending'])
            fieldwork.migrate(collector, run)
    else:
        fieldwork.migrate(collector, run)
    record['package'] = package(store, run)
    record['phase'] = 'archived'
    store.save(record)
    return {'run': run, 'state': 'archived', 'package': record['package']}


def finishable(row, reason):
    """Refuse, before anything durable is written, an ending that finish could not record."""
    if not reason or not reason.strip():
        raise Decision('Describe the engagement ending; a terminal callback alone is not that decision')
    if not row['binding'] and row['lifecycle']['capture']['value'] == 'displaced':
        raise Decision(f'{row["run"]}: its session was replaced and no replacement session has registered. Submit the '
                       'run marker from the replacement session before finishing; a displaced session alone cannot be finished')
    if not row['binding']:
        raise Decision('This packet has no associated session. An unused preparation is not a finished encounter')


def finish(store, row, reason, allow_incomplete=None):
    finishable(row, reason)
    if not row['record']:
        # A run predating operation records: collect its attributed connection's documents.
        connection = store.connection(row['connection'])
        row['record'] = {'schema': 'berman.operation/1', 'run': row['run'], 'phase': 'closing',
                         'project': str(connection.checkout), 'documents': connection.documents(),
                         'connection_attribution': {'id': connection.id, 'basis': row.get('connection_basis')},
                         'adopted_existing_run_at_utc': files.now()}
    record = row['record']
    record['started_session'] = row['binding']['session_id']
    allow_incomplete = allow_incomplete if allow_incomplete and allow_incomplete.strip() else None
    if record.get('ending') and not declared(record):
        record.setdefault('previous_ending_interpretations', []).append(record.pop('ending'))
    if not record.get('ending'):
        record['ending'] = declaration(reason, allow_incomplete, files.now())
        record['phase'] = 'closing'
        store.save(record)
    elif allow_incomplete and not record['ending'].get('allow_incomplete'):
        record['ending']['allow_incomplete'] = allow_incomplete
        record['ending']['override_recorded_at_utc'] = files.now()
        store.save(record)
    return preserve(store, record)
