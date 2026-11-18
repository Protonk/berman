"""The v1 command envelope and evidence inventory shared across the SSH boundary."""

import hashlib
import json
from pathlib import Path
import uuid

from berman import evidence

VERSION = 'berman.connector/1'
HEADER_LIMIT = 4 * 1024 * 1024
FILE_LIMIT = 256 * 1024 * 1024
TOTAL_LIMIT = 512 * 1024 * 1024
MEMBER_LIMIT = 4096


def identifier(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Expected a canonical UUID')
    return value


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def fingerprint(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def hash_file(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def inventory(rows):
    if not isinstance(rows, list) or len(rows) > MEMBER_LIMIT:
        raise ValueError('Invalid evidence inventory or too many members')
    result = {}
    total = 0
    for row in rows:
        name = evidence.relative(row['path'])
        if len(name.encode()) > 4000 or len(Path(name).parts) > 60:
            raise ValueError('Evidence path exceeds the portable recording bound')
        size = row['size']
        if name in result or type(size) is not int or not 0 <= size <= FILE_LIMIT:
            raise ValueError(f'Duplicate member or invalid byte length: {name}')
        if not evidence.SHA.fullmatch(row['sha256']):
            raise ValueError(f'Invalid member digest: {name}')
        result[name] = (size, row['sha256'])
        total += size
    if total > TOTAL_LIMIT:
        raise ValueError('Evidence exceeds total transfer bound')
    for name in result:
        if any(parent.as_posix() in result for parent in Path(name).parents):
            raise ValueError(f'File used as a parent of {name}')
    return result
