"""Exercise an installed wheel using only a copied example and disposable data.

Copy this file and examples/minimal-study outside the checkout, then run with the
wheel's interpreter. No imports from the test suite or access to source are needed.
Callbacks and access receipts are synthetic; no model session or trust is claimed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile

from berman.capture import adapters
from berman.files import read, write
from berman.installation import PACKAGE, FROZEN, FROZEN_SHA256, command
from berman.records import Store
from berman.workflow import Workflow


class SimulatedAccess:
    def __init__(self, store):
        self.store = store

    def preflight(self, connection=None):
        pass

    def ensure(self, run, folder):
        receipt = {'passed': True, 'fixture_only': True, 'meaning': 'No native access was checked.'}
        write(folder / 'readiness.json', receipt)
        return receipt


def smoke(example, base):
    project, study, unrelated = base / 'project', base / 'study with spaces', base / 'unrelated'
    project.mkdir()
    unrelated.mkdir()
    (project / 'README.md').write_text('Synthetic development project.\n')
    (project / 'AGENTS.md').write_text('Synthetic test only.\n')
    for args in (['init', '-q', '-b', 'main'], ['add', '.'],
                 ['-c', 'user.name=Synthetic', '-c', 'user.email=fixture@invalid',
                  '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'fixture']):
        subprocess.run(['git', '-C', str(project), *args], capture_output=True, check=True)

    def invoke(*args, expected=0):
        result = subprocess.run([str(Path(sys.executable).parent / 'berman'), *map(str, args)],
                                cwd=unrelated, text=True, capture_output=True)
        assert result.returncode == expected, (result.returncode, result.stdout, result.stderr)
        return json.loads(result.stdout)

    software = invoke('--json', 'verify', '--software')
    assert software['verdict'] == 'passed' and software['not_run'] == []
    initial = invoke('init', study, '--project', project, '--connection', 'example', '--codex', '/unconfigured/codex')
    assert initial['software']['files_sha256'][FROZEN] == FROZEN_SHA256
    for directory in ('recipes', 'plans'):
        shutil.copytree(example / directory, study / directory, dirs_exist_ok=True)
    config = read(study / 'berman.json')
    config['connections']['example']['plan'] = 'plans/example/plan.json'
    write(study / 'berman.json', config)
    store = Store(study)
    workflow = Workflow(store, SimulatedAccess(store))
    prepared = workflow.new()
    run = prepared['run']
    launch = shlex.split((study / f'operations/{run}/preparation/LAUNCH.txt').read_text())
    assert launch == command('--root', study, 'launch', '--run', run)

    source = store.home / 'sessions/synthetic.jsonl'
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'synthetic'}}) + '\n' +
                      json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant',
                                  'content': [{'text': 'Synthetic reply.'}]}}) + '\n')
    definition = adapters.definition(store, 'berman-codex')
    for event in ('UserPromptSubmit', 'Stop', 'SessionEnd'):
        payload = {'session_id': 'synthetic', 'turn_id': 'one', 'transcript_path': str(source),
                   'hook_event_name': event, 'prompt': 'Berman-Fieldwork-Run: ' + run}
        result = subprocess.run(shlex.split(definition['hooks'][event][0]['hooks'][0]['command']),
                                input=json.dumps(payload), cwd=unrelated, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert 'systemMessage' not in json.loads(result.stdout), result.stdout
    workflow.finish('Synthetic wheel test complete', run)
    assert invoke('--root', study, '--json', 'status')['archived'] == 1
    built = invoke('--root', study, 'archive')
    checked = invoke('--root', study, 'archive', '--verify')
    assert checked['archive_sha256'] == built['archive_sha256']
    with zipfile.ZipFile(study / built['archive_path']) as archive:
        names = archive.namelist()
        assert any('/runs/' in name for name in names)
        assert not any(name.endswith('berman.json') or '/.state/' in name or '/tools/' in name for name in names)
    diagnostic = invoke('--root', study, '--json', 'verify', expected=3)
    assert diagnostic['failed'] == []
    assert diagnostic['checks']['archive']['status'] == 'passed'
    assert diagnostic['not_run'] == ['native-access']
    assert 'native-capture:berman-codex' in diagnostic['not_established']
    return {'version': initial['software']['version'], 'package': str(PACKAGE),
            'source_sha256': initial['software']['source_sha256'],
            'frozen_sha256': hashlib.sha256((PACKAGE / FROZEN).read_bytes()).hexdigest(),
            'software_verification': software['verdict'], 'workspace_verification': diagnostic['verdict'],
            'archive_verified': True, 'callbacks': 'synthetic UserPromptSubmit, Stop, SessionEnd',
            'not_run': diagnostic['not_run'], 'not_established': diagnostic['not_established'],
            'never_checked': diagnostic['not_covered']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--example', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='berman-wheel-study-') as folder:
        print(json.dumps(smoke(args.example, Path(folder).resolve()), indent=2))
