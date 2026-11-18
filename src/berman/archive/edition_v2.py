"""Read published single-recording reading-edition/2 ZIPs and their selection keys."""

import json
import stat
import uuid
from berman import evidence, files


def selection(run, supplement=None):
    request = {'edition': 'connections', 'connections': [], 'runs': [run]}
    if supplement is not None:
        request['supplement'] = supplement
    return request


def edition_path(run, name):
    """Where a member of a recording appears in its reading edition."""
    if name == evidence.MANIFEST:
        return f'recordings/{run}/manifest.json'
    if name.startswith('runs/'):
        return name
    return f'recordings/{run}/{name}'


def verify(archive, manifest, prefix_expected, manifest_name):
    uuid.UUID(manifest['collection_id'])
    runs = manifest['runs']
    if (
        not isinstance(runs, list)
        or len(runs) != 1
        or not isinstance(runs[0], str)
        or not evidence.RUN.fullmatch(runs[0])
    ):
        raise ValueError('Guest reading edition requires one run')
    if not isinstance(manifest['summary'], dict) or any(
        type(value) is not int or value < 0 for value in manifest['summary'].values()
    ):
        raise ValueError('Invalid reading edition summary')
    prefix = manifest['namespace']
    if prefix != prefix_expected:
        raise ValueError('Reading edition manifest namespace differs')
    if archive.getinfo(manifest_name).external_attr >> 16 != stat.S_IFREG | 0o644:
        raise ValueError('Reading edition manifest mode differs')
    rows = manifest['members']
    if not isinstance(rows, list):
        raise ValueError('Reading edition members must be a list')
    by_path = {}
    for row in rows:
        path = row['path']
        evidence.relative(path.removesuffix('/') if row['type'] == 'directory' else path)
        if prefix + path == manifest_name or path in by_path:
            raise ValueError('Duplicate or self-referential edition member')
        mode = row['mode']
        expected_type = {'file': stat.S_IFREG, 'directory': stat.S_IFDIR, 'symlink': stat.S_IFLNK}.get(row['type'])
        if (
            type(mode) is not int
            or stat.S_IFMT(mode) != expected_type
            or (row['type'] == 'directory') != path.endswith('/')
        ):
            raise ValueError(f'Invalid edition member type or mode: {path}')
        info = archive.getinfo(prefix + path)
        raw = archive.read(info)
        if (
            info.external_attr >> 16 != mode
            or type(row['size']) is not int
            or len(raw) != row['size']
            or files.digest(raw) != row['sha256']
        ):
            raise ValueError(f'Reading edition member bytes or mode differ: {path}')
        by_path[path] = row
    if set(archive.namelist()) != {prefix + name for name in by_path} | {manifest_name}:
        raise ValueError('Reading edition ZIP membership differs from its manifest')
    inputs = manifest['inputs']
    for kind in ('originals', 'documents'):
        for row in inputs[kind]:
            if by_path.get(row['path']) != row:
                raise ValueError(f'Reading edition input differs from its member: {row["path"]}')
    if {r['path'] for r in inputs['originals']} != {p for p in by_path if p.startswith('runs/') and p != 'runs/'}:
        raise ValueError('Reading edition original membership differs from its inputs')
    layout = json.loads(archive.read(prefix + 'source-layout.json'))
    inventory = {entry['run']: entry for entry in layout['runs']}
    if set(inventory) != set(runs) or len(inventory) != len(layout['runs']):
        raise ValueError('Reading edition run membership differs from its inventory')
    run = runs[0]
    sources = inputs['recordings']
    if len(sources) != 1 or sources[0]['run'] != run:
        raise ValueError('Reading edition needs one recording for its run')
    item = sources[0]
    if item['basis'] != 'recording manifest':
        raise ValueError('Reading edition requires a split recording manifest')
    if item['path'] != edition_path(item['run'], evidence.MANIFEST):
        raise ValueError('Reading edition source manifest has a different path')
    raw = archive.read(prefix + item['path'])
    source = json.loads(raw)
    if (
        source.get('schema') != 'berman.recording/2'
        or source.get('collection_id') != manifest['collection_id']
        or source.get('run') != item['run']
    ):
        raise ValueError('Reading edition recording authority differs')
    recording_paths = {entry['path'] + ('/' if entry['type'] == 'directory' else '') for entry in source['entries']}
    modes = {name: by_path[edition_path(item['run'], name)]['mode'] for name in recording_paths}
    modes[evidence.MANIFEST] = by_path[item['path']]['mode']
    evidence.verify_members(source, modes, lambda name: archive.read(prefix + edition_path(run, name)))
    authorities = {item['path']}
    authorities.update(edition_path(item['run'], name) for name in recording_paths if not name.startswith('runs/'))
    if (
        manifest['selection'] != selection(source['run'], (source.get('supplement') or {}).get('sequence'))
        or inventory[source['run']] != source['custody']['inventory']
        or {p for p in recording_paths if p.startswith('runs/') and p != 'runs/'}
        != {row['path'] for row in inputs['originals']}
    ):
        raise ValueError('Reading edition inventory or selection differs from its recording')
    if files.digest(raw) != item['sha256']:
        raise ValueError(f'Reading edition recording input differs: {item["run"]}')
    groups = [{row['path'] for row in inputs[kind]} for kind in ('originals', 'documents')]
    if manifest['containers'] not in ([], ['runs/']):
        raise ValueError('Invalid reading edition namespace containers')
    groups += [
        authorities,
        set(manifest['generated']),
        set(manifest['containers']),
        {'source-layout.json', 'checksums.sha256'},
    ]
    if set.union(*groups) != set(by_path) or sum(len(group) for group in groups) != len(by_path):
        raise ValueError('Reading edition inputs and generated outputs do not partition its members')
    declared = {stream['path']: stream['format'] for stream in source['streams']}
    streams = {stream['path']: stream['format'] for stream in inputs['streams']}
    expected = {
        path: declared.get(path, 'unknown')
        for path in groups[0]
        if by_path[path]['type'] == 'file' and path.endswith('.jsonl')
    }
    if len(streams) != len(inputs['streams']) or streams != expected:
        raise ValueError('Reading edition streams differ from its recording manifest')
    reader = json.loads(archive.read(prefix + 'review/EDITION.json'))
    if inputs['reader'] != reader or reader.get('collection_id') != manifest['collection_id']:
        raise ValueError('Reading edition reader input differs')
    return source, files.digest(raw)
