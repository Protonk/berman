"""Install the wheel on stdin into a CI-only environment named for its digest. Runs on either host."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import zipfile


def install(system, filename, digest):
    if platform.system() != system or sys.version_info[:2] != (3, 14):
        raise ValueError(f'Qualification requires {system} with Python 3.14')
    data = sys.stdin.buffer.read()
    if hashlib.sha256(data).hexdigest() != digest or Path(filename).name != filename:
        raise ValueError('Candidate wheel transfer differs from build')
    root = Path.home() / '.local/share/berman-ci/wheels' / digest
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    python = root / 'runtime/bin/python'
    with (root / 'install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        wheel = root / filename
        if not (root / 'installed').exists():
            wheel.write_bytes(data)
            subprocess.run([sys.executable, '-I', '-m', 'venv', root / 'runtime'], check=True, stdout=sys.stderr)
            subprocess.run(
                [python, '-I', '-m', 'pip', 'install', '--disable-pip-version-check', wheel],
                check=True,
                stdout=sys.stderr,
            )
        with zipfile.ZipFile(wheel) as archive:
            expected = {
                n.removeprefix('berman/'): hashlib.sha256(archive.read(n)).hexdigest()
                for n in archive.namelist()
                if n.startswith('berman/') and n.endswith(('.py', '.json'))
            }
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != digest:
            raise ValueError('Stored candidate wheel differs from build')
        identity = json.loads(subprocess.check_output([python, '-I', '-m', 'berman', 'version']))
        if identity['files_sha256'] != expected:
            raise ValueError('Installed Berman files differ from the candidate wheel')
        subprocess.run([python, '-I', '-m', 'berman', 'verify'], check=True, stdout=sys.stderr)
        (root / 'installed').touch()
    print(json.dumps({'wheel_sha256': digest, 'source_sha256': identity['source_sha256'], 'python': str(python)}))


if __name__ == '__main__':
    os.umask(0o077)
    install(*sys.argv[1:])
