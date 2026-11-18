"""Package guest reading editions and verify them from their retained members."""
from contextlib import contextmanager
import fcntl
import time
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

from berman import evidence, files
from berman.archive import edition_v2

PREFIX = "berman-analysis/"

MANIFEST = 'edition-manifest.json'
SCHEMA = 'berman.reading-edition/3'
FILE_MODE = stat.S_IFREG | 0o644


def make_zip(payload, prefix):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, (data, mode) in sorted(payload.items()):
            info = zipfile.ZipInfo(prefix + path, date_time=(2026, 10, 22, 0, 0, 0))
            info.create_system = 3
            info.external_attr = mode << 16
            if stat.S_ISDIR(mode):
                info.external_attr |= 0x10
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    return stream.getvalue()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def seal(archive, aids, reader):
    """Keep recording members at their original paths. Only the reading aids need a second inventory."""
    payload = {i.filename: (archive.read(i), i.external_attr >> 16) for i in archive.infolist()}
    manifest = {
        'schema': SCHEMA,
        'recording_manifest_sha256': files.digest(payload[evidence.MANIFEST][0]),
        'reader_sha256': reader,
        'reading_aids': {name: files.digest(data) for name, data in aids.items()},
    }
    for name, data in aids.items():
        payload[name] = (data, FILE_MODE)
    payload[MANIFEST] = (json_bytes(manifest), FILE_MODE)
    return make_zip(payload, PREFIX)


def verify_current(archive, manifest):
    def read(name):
        return archive.read(PREFIX + name)

    raw = read(evidence.MANIFEST)
    digest = files.digest(raw)
    if digest != manifest['recording_manifest_sha256']:
        raise ValueError('Reading edition selects a different recording manifest')
    source = json.loads(raw)
    reader = manifest['reader_sha256']
    if not isinstance(reader, dict) or not reader or not all(evidence.SHA.fullmatch(v) for v in reader.values()):
        raise ValueError('Reading edition needs its reader source digests')
    aids = manifest['reading_aids']
    if not isinstance(aids, dict) or not {'ARCHIVE-GUIDE.txt', f'review/{source["run"]}.txt'} <= aids.keys():
        raise ValueError('Reading edition lacks its guide or run reader')
    for name, expected in aids.items():
        evidence.relative(name)
        if name not in ('ARCHIVE-GUIDE.txt', 'manifest.csv') and not re.fullmatch(r'review/[^/]+', name):
            raise ValueError(f'Invalid reading aid location: {name}')
        if archive.getinfo(PREFIX + name).external_attr >> 16 != FILE_MODE or files.digest(read(name)) != expected:
            raise ValueError(f'Reading aid bytes or mode differ: {name}')
    members = {i.filename.removeprefix(PREFIX): i.external_attr >> 16 for i in archive.infolist()
               if i.filename != PREFIX + MANIFEST and i.filename.removeprefix(PREFIX) not in aids}
    evidence.verify_members(source, members, read)
    return source, digest


def inspect_edition(data, expected_sha256=None):
    """Verify a detached edition against its retained recording and reader inventory."""
    digest = files.digest(data)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError('Reading edition ZIP digest differs from its published identity')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or not all(name.startswith(PREFIX) for name in names):
                raise ValueError('Reading edition requires unique members under its namespace')
            name = PREFIX + MANIFEST
            if archive.getinfo(name).external_attr >> 16 != FILE_MODE:
                raise ValueError('Reading edition manifest mode differs')
            manifest = json.loads(archive.read(name))
            if manifest['schema'] == SCHEMA:
                source, source_digest = verify_current(archive, manifest)
            elif manifest['schema'] == 'berman.reading-edition/2':
                source, source_digest = edition_v2.verify(archive, manifest, PREFIX, name)
            else:
                raise ValueError('Unsupported reading edition schema')
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, UnicodeError, RuntimeError, NotImplementedError) as exc:
        raise ValueError(f'Malformed reading edition: {exc}') from exc
    report = {'mode': 'verified', 'collection_id': source['collection_id'], 'run': source['run'],
              'archive_bytes': len(data), 'archive_sha256': digest, 'recording_manifest_sha256': source_digest,
              'original_files': sum(row['type'] == 'file' and row['path'].startswith('runs/')
                                    for row in source['entries'])}
    if 'supplement' in source:
        report['supplement'] = source['supplement']['sequence']
    return report


def verify_edition(path, expected_sha256=None):
    path = Path(path)
    if expected_sha256 is None and re.fullmatch(r'[0-9a-f]{64}\.zip', path.name):
        expected_sha256 = path.stem
    result = inspect_edition(path.read_bytes(), expected_sha256)
    return dict(result, archive_path=str(path))


def pointer_path(run, supplement=None):
    suffix = '' if supplement is None else f'-supplement-{supplement}'
    return f'archive/editions/selections/{run}{suffix}.json'


def existing_publication(root, receipt, source_digest):
    """Read the immutable pointer for this occurrence, including previously published guest editions."""
    run, supplement = receipt['run'], receipt.get('supplement')
    current = pointer_path(run, supplement)
    prior_selection = edition_v2.selection(run, supplement)
    prior = 'archive/editions/selections/' + files.digest(json_bytes(prior_selection)) + '.json'
    pointers = [p for p in (current, prior) if os.path.lexists(root / p)]
    if not pointers:
        return None
    if len(pointers) != 1:
        raise ValueError('Conflicting edition pointers for this occurrence')
    name = pointers[0]
    pointer = files.read(root / name)
    digest = pointer['sha256']
    if not isinstance(digest, str) or not evidence.SHA.fullmatch(digest):
        raise ValueError('Invalid published reading edition digest')
    path = f'archive/editions/{digest}.zip'
    if name == prior:
        if (pointer['schema'] != 'berman.edition-selection/1' or pointer['selection'] != prior_selection
                or pointer['path'] != path):
            raise ValueError('Reading edition selection pointer differs')
    elif pointer['schema'] != 'berman.edition-selection/2':
        raise ValueError('Unsupported reading edition pointer')
    return verified_publication(root, receipt, source_digest, digest, name)


def verified_publication(root, receipt, source_digest, digest, name):
    path = f'archive/editions/{digest}.zip'
    result = verify_edition(root / path, digest)
    if (result['collection_id'] != receipt['collection_id'] or result['run'] != receipt['run']
            or result.get('supplement') != receipt.get('supplement')
            or result['recording_manifest_sha256'] != source_digest):
        raise ValueError('Published edition selects a different recording')
    return dict(result, archive_path=path, selection_pointer=name)


def install(root, name, data):
    """Install complete immutable bytes, or verify an identical prior installation."""
    evidence.relative(name)
    root = Path(root)
    target = root / name
    if target.is_symlink() or any(p.is_symlink() for p in target.parents if p != root.parent):
        raise ValueError(f'Unsafe edition publication destination: {name}')
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.edition-', dir=target.parent)
    stage = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        stage.chmod(0o644)
        try:
            os.link(stage, target)
        except FileExistsError:
            if not target.is_file() or target.read_bytes() != data:
                raise ValueError(f'Existing immutable edition differs: {name}') from None
    finally:
        stage.unlink(missing_ok=True)


@contextmanager
def publication_lock(root):
    directory = Path(root) / 'archive/editions'
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / '.publication.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError('Another reading publication is in progress; retry this command') from None
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def publish_locked(root, receipt, source_digest, data):
    digest = files.digest(data)
    path = f'archive/editions/{digest}.zip'
    install(root, path, data)
    name = pointer_path(receipt['run'], receipt.get('supplement'))
    result = verified_publication(root, receipt, source_digest, digest, name)
    install(root, name, json_bytes({'schema': 'berman.edition-selection/2', 'sha256': digest}))
    return result
