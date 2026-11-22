"""Publish a portable episode record and prepare a reading folder from selected records."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
from uuid import UUID

from berman import files, project, snapshots

MANIFEST = 'manifest.json'
SCHEMA = 'berman.episode-record/1'


def entries(root):
    """Name unreadable entries and links, including links encountered as directories."""
    files.safe_directory(root)
    errors = []
    for parent, directories, names in os.walk(root, onerror=errors.append, followlinks=False):
        directories.sort()
        for name in sorted(directories + names):
            path = Path(parent) / name
            try:
                mode = path.lstat().st_mode
                if stat.S_ISDIR(mode):
                    continue
                problem = None if stat.S_ISREG(mode) else 'symbolic link or special file'
            except OSError as exc:
                problem = str(exc)
            yield path, problem
    for error in errors:
        yield Path(error.filename), str(error)


def hashes(folder):
    members = {}
    for path, problem in entries(folder):
        name = str(path.relative_to(folder))
        if problem:
            raise ValueError(f'Cannot verify {name}: {problem}')
        if name != MANIFEST:
            with files.regular(path).open('rb') as stream:
                members[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return members


def verify(record, expected=None):
    raw = files.regular(record / MANIFEST).read_bytes()
    fingerprint = files.digest(raw)
    if expected is not None and expected != fingerprint:
        raise ValueError('The supplied record digest differs')
    manifest = json.loads(raw)
    if (
        not isinstance(manifest, dict)
        or manifest.get('schema') != SCHEMA
        or not isinstance(manifest.get('episode'), str)
        or str(UUID(manifest['episode'])) != manifest['episode']
    ):
        raise ValueError('Expected an episode record')
    actual = hashes(record)
    if (
        actual != manifest['members']
        or not {'episode.json', 'opening.md', 'ending.json', 'coverage.json'} <= actual.keys()
    ):
        raise ValueError('Record members differ from the manifest')
    state = files.read(record / 'episode.json')
    if (
        state['id'] != manifest['episode']
        or state['schema'] != 'berman.episode/1'
        or state['opening'].encode() != (record / 'opening.md').read_bytes()
    ):
        raise ValueError('The recorded episode differs from its opening or manifest')
    return {
        'episode': manifest['episode'],
        'record': str(record),
        'sha256': fingerprint,
        'coverage': files.read(record / 'coverage.json'),
    }


def seal(folder, state, limitations):
    record = folder / 'record'
    with files.new_directory(record) as stage:
        for name in ('episode.json', 'ending.json'):
            files.write(stage / name, files.read(folder / name))
        files.atomic(stage / 'opening.md', state['opening'].encode())
        try:
            files.write(stage / 'final-project.json', project.git_state(state['project']))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            limitations.append(f'Final checkout observation unavailable: {exc}')
        roots = [
            ('return', folder / 'return'),
            ('sittings', folder / 'sittings'),
            ('telemetry/events', folder / 'telemetry/events'),
        ]
        session = folder / 'telemetry/session'
        try:
            captured = snapshots.read(session)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            limitations.append(f'Could not verify the staged parent transcript: {exc}')
            captured = None
        if captured:
            meta = captured['metadata']
            if meta['unparsed_jsonl_lines']:
                limitations.append(f'Unparsed parent JSONL lines: {meta["unparsed_jsonl_lines"]}')
            if meta['omitted_partial_tail_bytes']:
                limitations.append('An incomplete final JSONL line was retained separately.')
            if meta['last_assistant_message_present'] is False:
                limitations.append('The expected final assistant message was absent from the captured transcript.')
            roots.append(('telemetry/parent', session / 'snapshots' / snapshots.pointer(session)['generation']))
            for name in ('displaced', 'tails'):
                roots.append(('telemetry/' + name, session / name))
        else:
            limitations.append('No verified parent transcript was collected.')
        if not (folder / 'telemetry/events').exists():
            limitations.append('No session callbacks were observed.')
        if not (folder / 'sittings').exists():
            limitations.append('No sitting was launched.')
        retained = 0
        count = 0
        for prefix, root in roots:
            if not root.exists() and not root.is_symlink():
                if prefix == 'return':
                    limitations.append('The actor return directory was missing.')
                continue
            if not root.is_dir() or root.is_symlink():
                limitations.append(f'{prefix} was unavailable as a directory.')
                continue
            for path, problem in entries(root):
                label = str(Path(prefix) / path.relative_to(root))
                if not problem:
                    try:
                        size = path.stat().st_size
                        if count >= 4096 or retained + size > 1024 * 1024 * 1024:
                            raise ValueError('episode record size or file limit reached')
                        raw = files.stable_bytes(path, files.FILE_LIMIT)
                    except (OSError, ValueError) as exc:
                        problem = str(exc)
                if problem:
                    limitations.append(f'Omitted {label}: {problem}')
                    continue
                files.keep(stage / label, raw)
                retained += len(raw)
                count += 1
                if prefix == 'sittings' and files.read(stage / label).get('exit') is None:
                    limitations.append(f'{label}: process exit unobserved.')
                if prefix == 'telemetry/events':
                    detail = files.read(stage / label).get('limitation')
                    if detail:
                        limitations.append(f'{label}: {detail}')
        coverage = {
            'limitations': limitations,
            'scope': 'The actor return, observed sittings, bound parent session '
            'and checkout state at the boundaries. Other conversations and activity between episodes are outside capture.',
        }
        files.write(stage / 'coverage.json', coverage)
        files.write(stage / MANIFEST, {'schema': SCHEMA, 'episode': state['id'], 'members': hashes(stage)})
        verify(stage)
    return verify(record)


def review(records, question, output):
    if not question.strip():
        raise ValueError('Supply the question for the selected records')
    for record in records:
        if output.is_relative_to(record) or record.is_relative_to(output):
            raise ValueError('Use a review destination outside each source record')
    seen = set()
    with files.new_directory(output) as stage:
        sources = []
        for record in records:
            source = verify(record)
            ident = source['episode']
            if ident in seen:
                raise ValueError(f'Episode selected twice: {ident}')
            seen.add(ident)
            relative = Path('sources') / ident
            destination = stage / relative
            shutil.copytree(record, destination, symlinks=True)
            verify(destination, source['sha256'])
            sources.append({'episode': ident, 'path': str(relative), 'sha256': source['sha256']})
        files.write(stage / 'sources.json', sources)
        files.atomic(stage / 'question.md', (question.rstrip() + '\n').encode())
        files.atomic(
            stage / 'answer.md',
            b'<!-- Write your answer here. Cite the relative source path and passage '
            b'for each conclusion, and identify gaps that affect your answer. -->\n',
        )
    return {
        'output': str(output),
        'question': str(output / 'question.md'),
        'answer': str(output / 'answer.md'),
        'sources': sources,
    }
