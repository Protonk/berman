"""Shared file, JSON, digest and time operations for custody and native capture."""

from datetime import datetime, timezone
import hashlib
import json
import os
import fcntl
from contextlib import contextmanager
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
            raise Decision(f'Existing artifact differs; retained for inspection: {path}')
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
            raise Decision('A sitting or close is in progress') from None
        yield stream


@contextmanager
def new_directory(destination):
    """Publish a complete new directory. Concurrent Berman publishers serialize the rename."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise Decision(f'Destination already exists: {destination}')
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
                raise Decision(f'Destination already exists: {destination}')
            tree.rename(destination)
            os.fsync(fd)
        finally:
            os.close(fd)
