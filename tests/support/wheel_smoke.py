"""Exercise an installed wheel using only a copied example and disposable data.

Copy this file and examples/minimal-study outside the checkout, then run with the
wheel's interpreter. No imports from the test suite or access to source are needed.

A synthetic guest study is commissioned and closed through the controller's own entry
point and its version 1 command contract, with a hand-made collection receipt standing
in for the executor. The readers then verify the resulting recording and edition from
copies. No harness runs, no hook is installed and no trust is claimed.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from berman.installation import PACKAGE

PROTOCOL = 'berman.connector/1'
STUDY = 'smoke-study'
# Printed by an isolated interpreter: every module that importing the hook entry point loads and that
# is neither standard library nor Berman's. What the interpreter itself loads at startup is not counted.
HOOK_MODULES = """
import sys
before = set(sys.modules)
import berman.executor
print(sorted(m for m in set(sys.modules) - before if m.partition('.')[0] not in {*sys.stdlib_module_names, 'berman'}))
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def digest_json(value):
    """The assignment digest receipts carry: sorted keys and json.dumps default separators."""
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def smoke(example, base):
    unrelated, home = base / 'unrelated', base / 'guest home'
    study = home / 'research' / STUDY
    unrelated.mkdir()
    study.mkdir(parents=True)
    bin_dir = Path(sys.executable).parent

    def invoke(*args, expected=0, program='berman'):
        result = subprocess.run(
            [str(bin_dir / program), *map(str, args)], cwd=unrelated, text=True, capture_output=True
        )
        assert result.returncode == expected, (result.returncode, result.stdout, result.stderr)
        return result

    software = json.loads(invoke('--json', 'verify').stdout)
    assert software['passed'] and all(check['passed'] for check in software['checks'].values()), software
    identity = json.loads(invoke('version').stdout)
    environment = identity['environment']
    assert environment['dependencies']['jsonschema'] and environment['dependencies']['packaging'], environment
    assert 'capture/codex.py' in identity['files_sha256']
    # A hook process loads the standard library and Berman's own modules, and nothing third-party.
    loaded = subprocess.run(
        [sys.executable, '-I', '-B', '-c', HOOK_MODULES], cwd=unrelated, text=True, capture_output=True
    )
    assert loaded.returncode == 0 and loaded.stdout.strip() == '[]', (loaded.stdout, loaded.stderr)
    # Both names of the connector and the executor's own entry point start from the wheel.
    assert 'commission' in invoke('--help', program='berman-research').stdout
    assert 'commission' in invoke('remote', '--help').stdout
    assert 'codex-hooks' in invoke('executor', '--help').stdout
    collection = str(uuid.uuid4())
    (study / 'collection.json').write_text(
        json.dumps({'schema': 'berman.collection/1', 'id': collection, 'name': 'Wheel smoke'})
    )
    (study / 'connections/example').mkdir(parents=True)
    (study / 'connections/example/connection.json').write_text(
        json.dumps(
            {'schema': 'berman.connection/1', 'id': 'example', 'documents': ['README.md'], 'default_recipe': 'baseline'}
        )
    )
    shutil.copytree(example / 'recipes', study / 'recipes')

    def guest(command, body=b'', mutation=True, **fields):
        """One controller invocation, as the connector's SSH command starts it: a JSON line, then any bytes."""
        request = dict(protocol=PROTOCOL, collection_id=collection, command=command, **fields)
        if mutation:
            request['operation'] = str(uuid.uuid4())
        header = json.dumps(request, sort_keys=True, separators=(',', ':')).encode() + b'\n'
        result = subprocess.run(
            [sys.executable, '-I', '-m', 'berman.controller', '--study', STUDY],
            input=header + body,
            cwd=unrelated,
            capture_output=True,
            env=dict(os.environ, HOME=str(home)),
        )
        assert result.returncode == 0, (command, result.stderr.decode())
        return request, json.loads(result.stdout)

    _, commissioned = guest('commission', connection='example', harness='codex')
    assignment = commissioned['result']
    run = assignment['run']
    assert assignment['instructions'].count('{{notes_directory}}') == 1 and run == 'run-001', assignment
    ending, _ = guest('ending', run=run, reason='Synthetic wheel test complete')
    note = b'A synthetic account written for the wheel smoke.\n'
    member = {'path': 'packet/notes/account.md', 'size': len(note), 'sha256': digest(note)}
    observation = {key: assignment[key] for key in ('run', 'collection_id', 'operation', 'harness', 'connection')}
    observation.update(
        schema='berman.collection/1',
        assignment_sha256=digest_json(assignment),
        ending_id=ending['operation'],
        limitations=['Synthetic collection: no native session was run or observed.'],
        members=[member],
    )
    guest('capture', run=run, observation=observation)
    guest('transfer', body=note, run=run, **member)
    guest('accept', run=run)
    _, preserved = guest('preserve', run=run)
    _, published = guest('publish', run=run)
    _, verified = guest('verify', mutation=False, run=run)
    assert verified['result']['edition']['mode'] == 'verified', verified
    assert observation['limitations'][0] in verified['result']['recording']['coverage']['unknown'], verified

    # The artifacts verify from copies, with the readers and nothing of the study beside them.
    recording, edition = preserved['result'], published['result']
    package = base / 'copies/recording.zip'
    package.parent.mkdir()
    shutil.copyfile(study / recording['relative_path'], package)
    copied_edition = package.with_name(edition['archive_sha256'] + '.zip')
    shutil.copyfile(study / edition['archive_path'], copied_edition)
    shutil.rmtree(home)
    detached = json.loads(invoke('recording', 'verify', package, '--sha256', recording['package_sha256']).stdout)
    assert detached['integrity'] == 'passed' and detached['run'] == run, detached
    view = base / 'portable source'
    invoke('recording', 'materialize', package, view, '--sha256', recording['package_sha256'])
    assert json.loads(invoke('recording', 'verify', view).stdout)['manifest_sha256'] == detached['manifest_sha256']
    assert (view / 'runs' / run / 'packet/notes/account.md').read_bytes() == note
    checked = json.loads(invoke('archive', '--path', copied_edition).stdout)
    assert checked['archive_sha256'] == edition['archive_sha256'], checked
    return {
        'version': identity['version'],
        'package': str(PACKAGE),
        'source_sha256': identity['source_sha256'],
        'software_verification': software['passed'],
        'python': environment['python'],
        'dependencies': environment['dependencies'],
        'hooks_load_third_party_modules': False,
        'run': run,
        'guest_verification': verified['result']['edition']['mode'],
        'detached_recording_and_edition_verified': True,
        'portable_working_copy_verified': True,
        'collection': 'synthetic receipt; no harness, hook or native session',
        'coverage_unknown': verified['result']['recording']['coverage']['unknown'],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--example', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='berman-wheel-study-') as folder:
        print(json.dumps(smoke(args.example, Path(folder).resolve()), indent=2))
