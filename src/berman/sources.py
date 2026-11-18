"""Bounded acquisition of associated native sessions before a packet is inventoried.

Native relationship metadata selects sources, never conversation text or file recency.
Only a complete set of associated files enters the frozen collection.
"""

import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from berman import files
from berman.files import Decision

IDENTITY = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}')
MAX_BYTES = 512 * 1024 * 1024
MAX_SESSIONS = 256


def metadata(path):
    with files.regular(path).open('rb') as stream:
        line = stream.readline(128 * 1024 + 1)
    if len(line) > 128 * 1024:
        return None
    try:
        value = json.loads(line)
    except ValueError, UnicodeError:
        return None
    if not isinstance(value, dict) or value.get('type') != 'session_meta':
        return None
    value = value.get('payload')
    if not isinstance(value, dict):
        return None
    ident = value.get('id') or value.get('session_id')
    source = value.get('source')
    subagent = source.get('subagent') if isinstance(source, dict) else None
    spawn = subagent.get('thread_spawn') if isinstance(subagent, dict) else None
    parents = [value.get('parent_thread_id'), value.get('forked_from_id')]
    if isinstance(spawn, dict):
        parents.append(spawn.get('parent_thread_id'))
    parents = sorted(p for p in parents if isinstance(p, str) and IDENTITY.fullmatch(p))
    if not isinstance(ident, str) or not IDENTITY.fullmatch(ident):
        return None
    return {'session_id': ident, 'parents': parents, 'source': source}


def candidates(home, parent, harness):
    if harness == 'codex':
        result = []
        inspected = 0
        for path in sorted((home / 'sessions').rglob('*.jsonl')):
            if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(home)):
                continue
            inspected += 1
            if inspected > 50000:
                raise Decision('Native session discovery exceeds 50,000 files; narrow the native session home')
            value = metadata(path)
            if value:
                result.append((path, value))
        selected, seen = [], {parent}
        while True:
            added = [(p, v) for p, v in result if v['session_id'] not in seen and seen.intersection(v['parents'])]
            if not added:
                break
            ids = [v['session_id'] for _, v in added]
            if len(ids) != len(set(ids)):
                raise Decision('Several native files claim the same associated session identity')
            selected.extend(added)
            seen.update(ids)
            if len(selected) > MAX_SESSIONS:
                raise Decision('Associated session discovery exceeds 256 sources')
        return selected, inspected
    # Claude's supported layout explicitly nests subagents under the parent session identity.
    paths = sorted((home / 'projects').glob(f'*/{parent}/subagents/agent-*.jsonl'))
    selected = []
    for path in paths:
        if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(home)):
            raise Decision(f'Associated native source passes through a symlink: {path}')
        selected.append(
            (path, {'session_id': path.stem, 'parents': [parent], 'source': 'Claude parent/subagents layout'})
        )
    return selected, len(paths)


def collect(home, parent, harness, target):
    if not IDENTITY.fullmatch(parent) or harness not in ('codex', 'claude'):
        raise Decision('Unsupported associated-session discovery identity or harness')
    staging = Path(tempfile.mkdtemp(prefix='.associated-', dir=target.parent))
    try:
        selected, inspected = candidates(home, parent, harness)
        if len(selected) > MAX_SESSIONS:
            raise Decision('Associated session discovery exceeds 256 sources')
        rows, total = [], 0
        for path, value in selected:
            before = files.regular(path).stat()
            if before.st_size > 256 * 1024 * 1024 or total + before.st_size > MAX_BYTES:
                raise Decision('Associated native sources exceed the 256 MiB/file or 512 MiB total bound')
            raw = path.read_bytes()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise Decision('Associated native source changed during collection; retry when idle')
            total += len(raw)
            name = value['session_id'] + '/transcript.jsonl'
            files.keep(staging / name, raw)
            files.write(
                staging / value['session_id'] / 'capture.json',
                {
                    'format': harness + '-jsonl/1',
                    'sha256': files.digest(raw),
                    'saved_bytes': len(raw),
                    'meaning': 'Associated native source observed at closeout; no claim of a completed child session.',
                },
            )
            rows.append(dict(value, path=name, original_path=str(path), bytes=len(raw), sha256=files.digest(raw)))
        receipt = {
            'schema': 'berman.associated-sessions/1',
            'parent_session_id': parent,
            'harness': harness,
            'observed_at_utc': files.now(),
            'native_root_available': home.is_dir(),
            'files_inspected': inspected,
            'sources': rows,
            'scope': 'Explicit native parent/fork metadata (Codex), or parent/subagents layout (Claude). '
            'Absence does not establish that no other sessions existed. Child completion is not inferred.',
        }
        files.write(staging / 'sources.json', receipt)
        os.rename(staging, target)
        return receipt
    finally:
        if staging.exists():
            shutil.rmtree(staging)
