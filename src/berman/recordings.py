"""Read-only audit of preserved originals, lifecycle authority and recording ZIPs.

No locks, collector commands, Git calls or writes. An old run without contemporary ending
or package evidence is unknown, never retrospectively declared complete. Inventory checks
honor the fields actually recorded by historical writers, including optional mtimes.
"""

from pathlib import Path
import json
import stat
import zipfile
import zlib

from berman import files
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
    raw = files.regular(root / 'session/transcript.jsonl').read_bytes()
    if files.digest(raw) != meta['sha256'] or len(raw) != meta['saved_bytes']:
        raise ValueError('capture metadata and transcript bytes disagree')
    if meta.get('reader_sha256'):
        if files.digest(files.regular(root / 'session/conversation.txt').read_bytes()) != meta['reader_sha256']:
            raise ValueError('capture metadata and conversation reader disagree')
    history = files.regular(root / 'session/captures.jsonl').read_bytes().splitlines()
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
    root = store.root / 'runs' / run
    result = {'run': run, 'problems': [], 'unknown': [], 'checked': []}

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
            builder.verify_zip(data, payload, package.get('namespace') or namespace(store, record))

    check('recording', recording)
    return result


def audit(store):
    """Every indexed or physically preserved run, including missing and unindexed roots."""
    layout = files.read(store.root / 'source-layout.json')
    entries = {}
    for entry in layout['runs']:
        run = entry['run']
        if not isinstance(run, str) or not RUN.fullmatch(run) or run in entries:
            raise ValueError('invalid or duplicate run in source-layout.json')
        entries[run] = entry
    identities = set(entries)
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
