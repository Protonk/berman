"""Shared file, JSON, digest, lock and time operations."""

from datetime import datetime, timezone
import hashlib
import json
import os
import fcntl
from contextlib import contextmanager
from pathlib import Path
import stat
import tempfile

FILE_LIMIT = 256 * 1024 * 1024


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
        raise ValueError(f'Symlink in path: {path}')
    return path


def stable_bytes(path, limit):
    """Read a regular file whole, refusing one that changes during the read."""
    before = regular(path).stat()
    if before.st_size > limit:
        raise ValueError(f'File exceeds {limit} bytes: {path}')
    with path.open('rb') as stream:
        raw = stream.read(before.st_size + 1)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns) or len(
        raw
    ) != after.st_size:
        raise ValueError(f'Source changed during observation: {path}')
    return raw


def atomic(path, data):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError(f'Symlink in destination: {path}')
    fd, temp = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def read(path):
    return json.loads(regular(Path(path)).read_text())


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
            raise ValueError(f'Existing artifact differs; retained for inspection: {path}')
    else:
        atomic(path, data)


def digest_json(value):
    """Digest a JSON value using sorted keys and the default separators."""
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def safe_directory(path):
    if not path.is_dir() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f'Expected a directory without symlinks: {path}')


@contextmanager
def lock(folder, name, blocking=True):
    safe_directory(folder)
    with regular(folder / name).open('rb') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise ValueError('A sitting or close is in progress') from None
        yield stream


@contextmanager
def new_directory(destination):
    """Publish a complete new directory. Concurrent Berman publishers serialize the rename."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError(f'Destination already exists: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    safe_directory(destination.parent)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='.berman-') as temporary:
        tree = Path(temporary) / 'new'
        tree.mkdir(mode=0o700)
        yield tree
        fd = os.open(destination.parent, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if destination.exists() or destination.is_symlink():
                raise ValueError(f'Destination already exists: {destination}')
            tree.rename(destination)
            os.fsync(fd)
        finally:
            os.close(fd)
