"""Read-only audit of preserved originals, lifecycle authority and recording ZIPs.

No locks, collector commands, Git calls or writes. An old run without contemporary ending
or package evidence is unknown, never retrospectively declared complete. Inventory checks
honor the fields actually recorded by historical writers, including optional mtimes.
"""

from pathlib import Path
import json
import os
import stat
import zipfile
import zlib
import shutil
import tempfile

from berman import evidence, files, snapshots
from berman._legacy.api import RUN
from berman.archive import builder
from berman.lifecycle import declared, journal_phase
from berman.preserve import namespace, recording_payload
from berman.records import capture_metadata

ERRORS = (
    OSError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    RuntimeError,
    EOFError,
    zipfile.BadZipFile,
    zlib.error,
)


def inventory(root, entry):
    """Verify the complete path set, bytes, modes and every recorded mtime."""
    files.safe_directory(root)
    expected = entry['entries']
    if not isinstance(expected, list):
        raise ValueError('inventory entries must be a list')
    indexed = {}
    for item in expected:
        name = item['path']
        if not isinstance(name, str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('unsafe inventory path')
        if name in indexed:
            raise ValueError(f'duplicate inventory path: {name}')
        indexed[name] = item
    actual = {item['path']: item for item in files.inventory(root)}
    if set(actual) != set(indexed):
        raise ValueError(
            f'inventory paths differ: missing {sorted(indexed.keys() - actual.keys())}; '
            f'unrecorded {sorted(actual.keys() - indexed.keys())}'
        )
    for name, observed in actual.items():
        recorded = indexed[name]
        keys = {'type', 'mode'} | ({'mtime_ns'} if 'mtime_ns' in recorded else set())
        keys |= (
            {'size', 'sha256'} if observed['type'] == 'file' else {'target'} if observed['type'] == 'symlink' else set()
        )
        if any(recorded.get(key) != observed.get(key) for key in keys):
            raise ValueError(f'preserved bytes or metadata differ: {name}')
    state = root.stat()
    for key, value in (('root_mode', oct(stat.S_IMODE(state.st_mode))), ('root_mtime_ns', state.st_mtime_ns)):
        if key in entry and entry[key] != value:
            raise ValueError(f'preserved root metadata differs: {key}')


def session(root, run):
    """Check recorded capture identities and frozen bytes without consulting a live source."""
    meta = capture_metadata(root, run)
    if meta is None:
        return ['no capture snapshot metadata was retained']
    binding = files.read(root / 'session/binding.json')
    if binding.get('run') != run or binding.get('session_id') != meta['session_id']:
        raise ValueError('capture metadata and session binding disagree')
    folder = snapshots.resolve(root / 'session')
    raw = files.regular(folder / 'transcript.jsonl').read_bytes()
    if files.digest(raw) != meta['sha256'] or len(raw) != meta['saved_bytes']:
        raise ValueError('capture metadata and transcript bytes disagree')
    if meta.get('reader_sha256'):
        if files.digest(files.regular(folder / 'conversation.txt').read_bytes()) != meta['reader_sha256']:
            raise ValueError('capture metadata and conversation reader disagree')
    history = files.regular(folder / 'captures.jsonl').read_bytes().splitlines()
    if not history or json.loads(history[-1]) != meta:
        raise ValueError('capture history and last snapshot metadata disagree')
    closed = []
    for name in ('finalized.json', 'ended.json'):
        path = root / 'session' / name
        if path.exists() or path.is_symlink():
            closure = files.read(path)
            if closure['capture_sha256'] != meta['sha256']:
                raise ValueError(f'{name} and transcript bytes disagree')
            for key, expected in (('run', run), ('session_id', meta['session_id']), ('saved_bytes', len(raw))):
                if key in closure and closure[key] != expected:
                    raise ValueError(f'{name} and capture {key} disagree')
            closed.append(name)
    return [] if closed else ['no capture closure record was retained']


def audit_run(store, run, entry):
    if manifest_path(store.root, run).exists():
        return audit_manifest(store, run)
    root = store.root / 'runs' / run
    result = {'run': run, 'problems': [], 'unknown': [], 'checked': [], 'unfinished': False}

    def check(name, action):
        try:
            action()
            result['checked'].append(name)
            return True
        except ERRORS as exc:
            result['problems'].append(f'{name}: {exc}')
            return False

    journal_path = store.root / 'migrations' / (run + '.json')
    if entry is None:
        try:
            journal = files.read(journal_path)
            phase = journal_phase(journal, run)
            if phase == 'indexed':
                raise ValueError('journal claims indexed but source-layout.json has no entry')
            entry = journal['entry']
            result['unfinished'] = True
            result['unknown'].append('migration inventory is not yet published in source-layout.json')
        except ERRORS as exc:
            result['problems'].append(f'preserved run has no source-layout.json inventory: {exc}')
            return result
    if entry.get('current_root') != 'runs/' + run:
        result['problems'].append('inventory current_root does not name this preserved run')
        return result
    intact = check('originals', lambda: inventory(root, entry))
    record = None
    try:
        record = store.record(run)
    except ERRORS as exc:
        result['problems'].append(f'operation record: {exc}')
    if intact:
        check('capture', lambda: result['unknown'].extend(session(root, run)))

    def migration():
        journal = files.read(journal_path)
        phase = journal_phase(journal, run)
        if journal['entry'] != entry:
            raise ValueError('journal and published inventory disagree')
        if phase != 'indexed':
            result['unfinished'] = True
            result['unknown'].append(f'migration is unfinished: {phase}')

    if journal_path.exists() or journal_path.is_symlink():
        check('migration', migration)
    elif record and record.get('phase') == 'archived':
        result['problems'].append('migration: archived operation has no migration journal')
    else:
        result['unknown'].append('no migration journal was retained')
    ending_path = root / 'context/berman/ending.json'
    if record is None:
        if ending_path.exists():
            result['problems'].append('preserved ending exists but its operation record is missing or unreadable')
        result['unknown'].extend(
            ['no operation record establishes an ending', 'no operation record establishes a recording ZIP']
        )
        return result
    if record.get('phase') != 'archived':
        result['unfinished'] = True
        result['unknown'].append(f'preservation is unfinished: operation phase {record.get("phase")}')
    if declared(record):

        def ending():
            if files.read(ending_path) != record['ending']:
                raise ValueError('operation and preserved ending declarations disagree')

        check('ending', ending)
    elif record.get('migration_authority') or record.get('ending'):
        result['unknown'].append('custody or an inferred ending is recorded, but no researcher ending declaration')
    else:
        result['problems'].append('ending: preserved operation has no researcher ending declaration')
    package = record.get('package')
    if not package:
        result['unknown'].append('no recording ZIP is recorded')
        return result

    def recording():
        path = store.path(package['path'])
        if not path.is_relative_to(store.root):
            raise ValueError('recording ZIP path is outside this workspace')
        data = files.regular(path).read_bytes()
        if files.digest(data) != package.get('sha256') or len(data) != package.get('bytes'):
            raise ValueError('recording ZIP differs from its recorded digest or size')
        sidecar = files.regular(path.with_suffix('.zip.sha256')).read_text()
        if sidecar != f'{files.digest(data)}  {path.name}\n':
            raise ValueError('recording ZIP checksum sidecar disagrees')
        if intact:
            payload = recording_payload(root, entry)
            if package.get('members') != len(payload):
                raise ValueError('recording ZIP member count disagrees')
            # This document is historical producer text, authenticated by the recorded ZIP
            # digest above. Its spelling is not a contract imposed by today's writer.
            prefix = package.get('namespace') or namespace(store, record)
            with zipfile.ZipFile(path) as archive:
                payload['RECORDING.txt'] = (archive.read(prefix + 'RECORDING.txt'), payload['RECORDING.txt'][1])
            builder.verify_zip(data, payload, package.get('namespace') or namespace(store, record))

    check('recording', recording)
    return result


def audit(store):
    """Every indexed or physically preserved run, including missing and unindexed roots."""
    layout_path = store.root / 'source-layout.json'
    layout = files.read(layout_path) if layout_path.exists() else {'runs': []}
    entries = {}
    for entry in layout['runs']:
        run = entry['run']
        if not isinstance(run, str) or not RUN.fullmatch(run) or run in entries:
            raise ValueError('invalid or duplicate run in source-layout.json')
        entries[run] = entry
    identities = set(entries)
    identities.update(path.parent.name for path in (store.root / 'recordings').glob('run-*/manifest.json'))
    parent = store.root / 'runs'
    if parent.exists():
        files.safe_directory(parent)
        identities.update(p.name for p in parent.iterdir() if RUN.fullmatch(p.name))
    # Losing both the packet and its inventory must not hide an archived operation.
    for path in store.operations.glob('run-*/operation.json'):
        if RUN.fullmatch(path.parent.name):
            value = files.read(path)
            if isinstance(value, dict) and (value.get('phase') == 'archived' or value.get('package')):
                identities.add(path.parent.name)
    return [audit_run(store, run, entries.get(run)) for run in sorted(identities)]


def audit_manifest(store, run):
    result = {'run': run, 'problems': [], 'unknown': [], 'checked': [], 'unfinished': False}
    try:
        path = manifest_path(store.root, run)
        manifest = evidence.validate(files.read(path))
        if manifest['run'] != run or manifest['collection_id'] != evidence.collection(store.root):
            raise ValueError('Manifest identity differs from its collection or run')
        inventory(store.root / 'runs' / run, manifest['custody']['inventory'])
        result['checked'].append('originals')
        manifest_members(store.root, manifest)
        result['checked'].append('retained evidence')
        result['unknown'].extend(manifest['coverage']['unknown'])
        publication = path.parent / 'publication.json'
        if not publication.exists():
            result['unfinished'] = True
            result['unknown'].append('Recording manifest exists but publication has not committed')
            return result
        published = files.read(publication)
        if published['manifest_sha256'] != evidence.identity(manifest):
            raise ValueError('Publication and recording manifest disagree')
        package = store.root / evidence.relative(published['relative_path'])
        report = evidence.verify_package(package, published['sha256'])
        if report['manifest'] != manifest:
            raise ValueError('Published package and retained manifest disagree')
        sidecar = files.regular(package.with_suffix('.zip.sha256')).read_text()
        if sidecar != f'{published["sha256"]}  {package.name}\n':
            raise ValueError('Recording checksum sidecar disagrees')
        result['checked'].append('recording')
    except ERRORS as exc:
        result['problems'].append(str(exc))
    return result


def manifest_path(root, run):
    if not RUN.fullmatch(run):
        raise ValueError('Invalid recording run identity')
    return Path(root) / 'recordings' / run / 'manifest.json'


def member_path(root, run, name):
    evidence.relative(name)
    if name == 'evidence' or name.startswith('evidence/'):
        return manifest_path(root, run).parent / name
    if not name.startswith('runs/' + run + '/') and name not in ('runs', 'runs/' + run):
        raise ValueError('Recording member is outside its run')
    return Path(root) / name


def manifest_members(root, manifest, authority_root=None):
    """Read the frozen source bytes and retained authority copies named by a manifest."""
    payload = {}
    for item in manifest['entries']:
        name, kind = item['path'], item['type']
        path = (
            authority_root / name
            if authority_root is not None and (name == 'evidence' or name.startswith('evidence/'))
            else member_path(root, manifest['run'], name)
        )
        data = (
            b''
            if kind == 'directory'
            else os.fsencode(item['target'])
            if kind == 'symlink'
            else files.regular(path).read_bytes()
        )
        payload[name + ('/' if kind == 'directory' else '')] = (data, evidence.mode(item))
    payload[evidence.MANIFEST] = (files.json_bytes(manifest), stat.S_IFREG | 0o644)
    evidence.verify_members(
        manifest, {name: mode for name, (_, mode) in payload.items()}, lambda name: payload[name][0]
    )
    return payload


def build_manifest(store, run, stage, historical=False, collection_id=None):
    """Assemble in caller-owned staging; inspecting historical originals writes nothing to them."""
    manifest_path(store.root, run)  # Validate the identity before using it in paths.
    layout = files.read(store.root / 'source-layout.json')
    entry = next((row for row in layout['runs'] if row['run'] == run), None)
    if entry is None:
        raise ValueError('Preservation requires the recorded source inventory')
    source = store.root / 'runs' / run
    inventory(source, entry)
    record = store.record(run)
    if not historical and (record is None or not declared(record)):
        raise ValueError('New recording publication requires an explicit ending declaration')
    authority = stage / 'evidence'
    authority.mkdir()
    refs = dict.fromkeys(('operation', 'migration', 'ending', 'capture', 'binding', 'closure'))
    for name, src in (
        ('operation', store.folder(run) / 'operation.json'),
        ('migration', store.root / 'migrations' / (run + '.json')),
    ):
        if src.exists():
            files.keep(authority / (name + '.json'), files.regular(src).read_bytes())
            refs[name] = 'evidence/' + name + '.json'
    base = 'runs/' + run + '/'
    capture_folder = snapshots.resolve(source / 'session')
    capture_relative = (capture_folder / 'capture.json').relative_to(source).as_posix()
    for name, relative in (
        ('ending', 'context/berman/ending.json'),
        ('capture', capture_relative),
        ('binding', 'session/binding.json'),
        ('closure', 'session/finalized.json'),
    ):
        if (source / relative).exists():
            refs[name] = base + relative
    if refs['closure'] is None and (source / 'session/ended.json').exists():
        refs['closure'] = base + 'session/ended.json'
    attribution = {'connection': None, 'basis': 'unattributed', 'evidence': None}
    if record and record.get('connection'):
        attribution = {
            'connection': record['connection']['id'],
            'basis': 'retained operation',
            'evidence': refs['operation'],
        }
    elif run in store.catalogue.runs:
        catalogue = files.regular(store.path(store.workspace['catalogue'])).read_bytes()
        rows = json.loads(catalogue)['runs']
        index = next(i for i, row in enumerate(rows) if row['run'] == run)
        files.keep(
            authority / 'attribution.json',
            files.json_bytes(
                {'source_sha256': files.digest(catalogue), 'source_pointer': f'/runs/{index}', 'record': rows[index]}
            ),
        )
        attribution = {
            'connection': rows[index]['connection'],
            'basis': 'retained catalogue row',
            'evidence': 'evidence/attribution.json',
        }
    elif (source / 'run.json').exists():
        connection = files.read(source / 'run.json').get('connection')
        if isinstance(connection, str):
            attribution = {'connection': connection, 'basis': 'retained packet metadata', 'evidence': base + 'run.json'}
    entries = [
        {'path': 'runs', 'type': 'directory', 'mode': '0o755'},
        {'path': 'runs/' + run, 'type': 'directory', 'mode': entry.get('root_mode', '0o755')},
        {'path': 'evidence', 'type': 'directory', 'mode': '0o755'},
    ]
    entries += [dict(item, path=base + item['path']) for item in entry['entries']]
    entries += [dict(item, path='evidence/' + item['path']) for item in files.inventory(authority)]
    unknown = [f'No historical {name} evidence was retained' for name, ref in refs.items() if ref is None]
    if attribution['connection'] is None:
        unknown.append('No explicit connection attribution was retained')
    streams = []
    paths = {row['path'] for row in entry['entries'] if row['type'] == 'file'}
    for relative in sorted(paths):
        if not relative.endswith('.jsonl'):
            continue
        if not relative.endswith('/transcript.jsonl'):
            streams.append({'path': base + relative, 'format': 'unknown', 'basis': None})
            continue
        meta_path = str(Path(relative).parent / 'capture.json')
        meta = files.read(source / meta_path) if meta_path in paths else {}
        fmt = meta.get('format') or (
            'claude-jsonl/1' if meta.get('harness') == 'claude' else 'codex-jsonl/1' if meta else 'unknown'
        )
        streams.append({'path': base + relative, 'format': fmt, 'basis': base + meta_path if meta else None})
        if fmt == 'unknown':
            unknown.append('Unrecognized stream format: ' + relative)
        for key in ('omitted_partial_tail_bytes', 'unparsed_jsonl_lines'):
            if meta.get(key):
                unknown.append(f'{relative}: {key}={meta[key]}')
        if meta.get('last_assistant_message_present') is False:
            unknown.append(relative + ': expected assistant message absent')
    if refs['closure']:
        closure = files.read(source / refs['closure'].removeprefix(base))
        boundary = closure.get('boundary') or {}
        if boundary.get('warnings') or boundary.get('override_reason'):
            unknown.append('Capture closure retained an explicit incomplete boundary: ' + str(boundary))
    manifest = {
        'schema': evidence.SCHEMA,
        'collection_id': collection_id or evidence.collection(store.root),
        'run': run,
        'profile': 'historical-import' if historical else 'native',
        'recorded_at_utc': files.now(),
        'meaning': 'Membership and retained evidence observed at publication; no historical event is inferred.',
        'entries': entries,
        'evidence': refs,
        'streams': streams,
        'attribution': attribution,
        'coverage': {'unknown': unknown},
        'custody': {
            'inventory': entry,
            'meaning': 'Original filesystem metadata; materializations have their own mtimes.',
        },
    }
    evidence.validate(manifest)
    files.keep(stage / 'manifest.json', files.json_bytes(manifest))
    return manifest


def export(store, run, destination, historical=False, collection_id=None):
    """A new portable package outside preserved originals; no indexes or source records change."""
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('Export destination must be new')
    if destination.is_relative_to(store.root / 'runs'):
        raise ValueError('An export cannot be written into preserved originals')
    files.safe_directory(destination.parent)
    path = manifest_path(store.root, run)
    with tempfile.TemporaryDirectory(prefix='.recording-export-', dir=destination.parent) as temp:
        stage = Path(temp)
        if path.exists():
            manifest = evidence.validate(files.read(path))
            inventory(store.root / 'runs' / run, manifest['custody']['inventory'])
            payload = manifest_members(store.root, manifest)
        else:
            manifest = build_manifest(store, run, stage, historical, collection_id)
            payload = manifest_members(store.root, manifest, authority_root=stage)
        data = builder.make_zip(payload, '')
        candidate = stage / 'package.zip'
        files.atomic(candidate, data)
        result = evidence.verify_package(candidate)
        # Recheck original custody before publication, including a concurrent source change.
        inventory(store.root / 'runs' / run, manifest['custody']['inventory'])
        os.link(candidate, destination)  # A competing export cannot replace an earlier publication.
    return dict(result, path=str(destination))


def publish(store, run, historical=False):
    """Retain immutable authority beside originals, then publish its self-contained package."""
    path = manifest_path(store.root, run)
    if path.exists():
        manifest = evidence.validate(files.read(path))
        if manifest['collection_id'] != evidence.collection(store.root) or manifest['run'] != run:
            raise ValueError('Recording manifest belongs to another collection or run')
    else:
        path.parent.parent.mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.' + run + '-', dir=path.parent.parent))
        try:
            manifest = build_manifest(store, run, stage, historical)
            manifest_members(store.root, manifest, authority_root=stage)
            stage.rename(path.parent)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    inventory(store.root / 'runs' / run, manifest['custody']['inventory'])
    payload = manifest_members(store.root, manifest)
    data = builder.make_zip(payload, '')
    destination = store.root / 'archive/recordings' / (run + '.recording-v2.zip')
    files.keep(destination, data)
    result = evidence.verify_package(destination)
    digest = result['package_sha256']
    files.keep(destination.with_suffix('.zip.sha256'), f'{digest}  {destination.name}\n'.encode())
    publication = {
        'path': str(destination),
        'relative_path': destination.relative_to(store.root).as_posix(),
        'schema': evidence.SCHEMA,
        'sha256': digest,
        'bytes': len(data),
        'members': len(payload),
        'namespace': '',
        'manifest': path.relative_to(store.root).as_posix(),
        'manifest_sha256': evidence.identity(manifest),
        'coverage': manifest['coverage'],
    }
    files.keep(
        path.parent / 'publication.json',
        files.json_bytes({key: value for key, value in publication.items() if key != 'path'}),
    )
    return publication
