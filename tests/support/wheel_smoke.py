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


# Printed by an isolated interpreter: every module that importing the hook entry points loads and that
# is neither standard library nor Berman's. What the interpreter itself loads at startup is not counted.
HOOK_MODULES = """
import sys
before = set(sys.modules)
import berman.capture.router, berman.capture.claude_router
print(sorted(m for m in set(sys.modules) - before if m.partition('.')[0] not in {*sys.stdlib_module_names, 'berman'}))
"""


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
    # The wheel carries its record schemas, and pip installed what it declares.
    assert {name: check['status'] for name, check in software['checks'].items()} == dict.fromkeys(
        ('installation', 'dependencies', 'frozen-collector', 'schemas'), 'passed'), software['checks']
    environment = software['checks']['installation']['details']['software']['environment']
    assert environment['dependencies']['jsonschema'] and environment['dependencies']['rich'], environment
    # A hook process loads the standard library and Berman's capture modules, and nothing third-party.
    loaded = subprocess.run([sys.executable, '-I', '-B', '-c', HOOK_MODULES], cwd=unrelated, text=True, capture_output=True)
    assert loaded.returncode == 0 and loaded.stdout.strip() == '[]', (loaded.stdout, loaded.stderr)
    initial = invoke('init', study, '--project', project, '--connection', 'example', '--codex', '/unconfigured/codex')
    assert initial['software']['files_sha256'][FROZEN] == FROZEN_SHA256
    for directory in ('recipes', 'plans'):
        shutil.copytree(example / directory, study / directory, dirs_exist_ok=True)
    config = read(study / 'berman.json')
    # Probe Python can be a system interpreter without Berman. Hooks must keep
    # the wheel's interpreter when the operator configures that separate path.
    config['python'] = sys._base_executable
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
    preserved = workflow.finish('Synthetic wheel test complete', run)
    package = preserved['package']
    detached = invoke('recording', 'verify', package['path'], '--sha256', package['sha256'])
    assert detached['integrity'] == 'passed' and detached['run'] == run
    view = base / 'portable source'
    invoke('recording', 'materialize', package['path'], view, '--sha256', package['sha256'])
    assert invoke('recording', 'verify', view)['manifest_sha256'] == detached['manifest_sha256']
    assert invoke('--root', study, '--json', 'status')['archived'] == 1
    built = invoke('--root', study, 'archive')
    checked = invoke('--root', study, 'archive', '--verify')
    assert checked['archive_sha256'] == built['archive_sha256']
    assert invoke('archive', '--path', study / built['archive_path'])['archive_sha256'] == built['archive_sha256']
    assert invoke('--root', study, 'archive', '--freshness')['freshness'] == 'current'
    with zipfile.ZipFile(study / built['archive_path']) as archive:
        names = archive.namelist()
        assert any('/runs/' in name for name in names)
        assert not any(name.endswith('berman.json') or '/.state/' in name or '/tools/' in name for name in names)
    diagnostic = invoke('--root', study, '--json', 'verify', expected=3)
    assert diagnostic['failed'] == []
    assert diagnostic['checks']['archive']['status'] == 'passed'
    assert diagnostic['checks']['recordings']['status'] == 'passed'
    assert diagnostic['not_run'] == ['native-access']
    assert 'native-capture:berman-codex' in diagnostic['not_established']
    return {'version': initial['software']['version'], 'package': str(PACKAGE),
            'source_sha256': initial['software']['source_sha256'],
            'frozen_sha256': hashlib.sha256((PACKAGE / FROZEN).read_bytes()).hexdigest(),
            'software_verification': software['verdict'], 'workspace_verification': diagnostic['verdict'],
            'python': environment['python'], 'dependencies': environment['dependencies'],
            'hooks_load_third_party_modules': False,
            'archive_verified': True, 'recordings_verified': True,
            'detached_recording_and_edition_verified': True, 'portable_working_copy_verified': True,
            'callbacks': 'synthetic UserPromptSubmit, Stop, SessionEnd',
            'not_run': diagnostic['not_run'], 'not_established': diagnostic['not_established'],
            'never_checked': diagnostic['not_covered']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--example', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='berman-wheel-study-') as folder:
        print(json.dumps(smoke(args.example, Path(folder).resolve()), indent=2))
