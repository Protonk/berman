"""Shared file, JSON, hashing and time operations; imports no other Berman module.

now, digest, json_bytes, regular, read_json and atomic reproduce the frozen collector's helpers
(capture_session.py) statement for statement, so retained bytes, digests and timestamps keep
their recorded form. test_boundaries.py compares their syntax trees, so even a docstring or a
reordered keyword argument in those six fails it. keep() compares bytes on retries. Other modules
call atomic through this module, never bound by name, so interruption tests can patch it.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


class Decision(ValueError):
    """A substantive choice or inconsistent record requires attention."""


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f'Expected a regular file: {path}')
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError(f'Symlink in capture path: {path}')
    return path


def read_json(path):
    return json.loads(regular(path).read_text())


def atomic(path, data):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError(f'Symlink in capture destination: {path}')
    fd, temp = tempfile.mkstemp(prefix='.capture-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def read(path):
    return read_json(Path(path))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic(path, json_bytes(value))


def keep(path, data):
    """Create a deterministic artifact, or verify an interrupted operation's copy."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if regular(path).read_bytes() != data:
            raise Decision(f'Existing artifact differs; retained for inspection: {path}')
    else:
        atomic(path, data)


def digest_json(value):
    """The recorded digest of a definition or plan: sorted keys and json.dumps default separators."""
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def safe_directory(path):
    if not path.is_dir() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f'Expected a directory without symlinks: {path}')


def metadata_matches(root, entries, root_mode=None, root_mtime_ns=None):
    """A brief recheck under a callback lock: compare recorded metadata without hashing bytes."""
    expected = {row['path']: row for row in entries}
    seen = set()
    st = root.lstat()
    if (root_mode is not None and oct(stat.S_IMODE(st.st_mode)) != root_mode) or (
            root_mtime_ns is not None and st.st_mtime_ns != root_mtime_ns):
        return False
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(parent) / name
            row = expected.get(path.relative_to(root).as_posix())
            if row is None:
                return False
            st = path.lstat()
            kind = 'symlink' if stat.S_ISLNK(st.st_mode) else 'directory' if stat.S_ISDIR(st.st_mode) else 'file'
            if (kind != row['type'] or oct(stat.S_IMODE(st.st_mode)) != row['mode']
                    or ('mtime_ns' in row and st.st_mtime_ns != row['mtime_ns'])
                    or (kind == 'file' and st.st_size != row['size'])
                    or (kind == 'symlink' and os.readlink(path) != row['target'])):
                return False
            seen.add(row['path'])
    return seen == set(expected)


def inventory(root):
    """Inventory bytes and metadata, including directory and symlink mtimes."""
    safe_directory(root)
    result = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(parent) / name
            before = path.lstat()
            item = {'path': path.relative_to(root).as_posix(), 'mode': oct(stat.S_IMODE(before.st_mode)),
                    'mtime_ns': before.st_mtime_ns}
            if stat.S_ISLNK(before.st_mode):
                item.update(type='symlink', target=os.readlink(path))
            elif stat.S_ISDIR(before.st_mode):
                item.update(type='directory')
            elif stat.S_ISREG(before.st_mode):
                data = path.read_bytes()
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != after.st_size:
                    raise ValueError(f'File changed during inventory: {path}')
                item.update(type='file', size=len(data), sha256=digest(data))
            else:
                raise ValueError(f'Unsupported file type: {path}')
            result.append(item)
    return sorted(result, key=lambda row: row['path'])
