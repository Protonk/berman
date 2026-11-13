"""Versioned worker guidance, its composition with a connection's commission, and project snapshots."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import berman_files as files
from berman_files import Decision, keep, read, write
from berman_legacy import MARKER
import fieldwork


PROTOCOLS = ('work-accounts/1', 'participant-inquiry/1')


def recipe(store, name, connection):
    """Recipe 2 wording is written for named connections and adds to the connection's commission."""
    if not re.fullmatch(r'[a-z][a-z0-9-]*', name):
        raise ValueError('Invalid recipe name')
    folder = store.root / 'recipes' / name
    meta = read(folder / 'recipe.json')
    if meta.get('id') != name or meta.get('schema') != 'berman.recipe/2' or meta.get('coverage') != ['notes']:
        raise ValueError('This workflow requires a named notes recipe')
    if meta.get('recording_protocol') not in PROTOCOLS:
        raise ValueError('Recipe must explicitly name a supported recording protocol')
    if connection.id not in meta.get('connections', []):
        raise Decision(f'Recipe {name} is worded for {", ".join(meta.get("connections", [])) or "no connection"}, '
                       f'not {connection.id}; introduce a separately versioned recipe')
    result = {'metadata': meta}
    for key in ('instructions', 'research_commission'):
        path = Path(meta[key])
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Recipe file must stay within its directory')
        result[key] = files.regular(folder / path).read_text()
    if result['instructions'].count('{{notes_directory}}') != 1:
        raise ValueError('Instructions must contain exactly one notes_directory placeholder')
    return result


def compose(connection_commission, research_commission):
    """The project commission, then the selected research instructions' commission."""
    return connection_commission.rstrip('\n') + '\n\n' + research_commission


def git_state(project):
    def git(*args):
        p = subprocess.run(['git', '-C', str(project), *args], check=True,
                           capture_output=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
        return p.stdout.decode()
    return {'head': git('rev-parse', 'HEAD').strip(),
            'status': git('status', '--porcelain=v1', '--untracked-files=normal')}


def snapshot(project, names, target, require_stable_git=True):
    """Preserve the first successful selection, including explicit missing-file records."""
    if target.exists():
        result = read(target / 'snapshot.json')
        for row in result['files']:
            if row.get('sha256') and files.digest(files.regular(target / row['path']).read_bytes()) != row['sha256']:
                raise Decision(f'Project snapshot has changed: {target / row["path"]}')
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    files.safe_directory(target.parent)
    staging = Path(tempfile.mkdtemp(prefix='.snapshot-', dir=target.parent))
    try:
        before = git_state(project)
        rows = []
        for name in names:
            rel = Path(name)
            if rel.is_absolute() or '..' in rel.parts or name == 'snapshot.json':
                raise ValueError(f'Invalid selected document: {name}')
            source = project / rel
            if not source.exists() and not source.is_symlink():
                rows.append({'path': name, 'missing': True})
                continue
            data = files.regular(source).read_bytes()
            keep(staging / rel, data)
            rows.append({'path': name, 'bytes': len(data), 'sha256': files.digest(data)})
        after = git_state(project)
        if require_stable_git and before != after:
            raise Decision('Project revision or working tree changed during the document snapshot; retry')
        for row in rows:
            source = project / row['path']
            if row.get('missing'):
                if source.exists():
                    raise Decision('A selected document appeared during the snapshot; retry')
            elif files.digest(files.regular(source).read_bytes()) != row['sha256']:
                raise Decision('A selected document changed during the snapshot; retry')
        result = {'schema': 'berman.project-snapshot/1', 'observed_at_utc': files.now(),
                  'project': str(project), **before, 'files': rows,
                  'git_after': after, 'git_changed_during_collection': before != after,
                  'scope': 'Selected documents and observed Git state at collection time; not a full checkout snapshot.'}
        write(staging / 'snapshot.json', result)
        staging.rename(target)
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def prepare(store, record):
    run = record['run']
    folder = store.folder(run)
    project = Path(record['project'])
    packet = store.field / run
    if record.get('packet', str(packet)) != str(packet) or packet.parent != store.field:
        raise Decision(f'{run}: packets must stay directly within the shared packet root {store.field}')
    context = snapshot(project, record['documents'], folder / 'preparation/initial-project')
    instructions = record['recipe']['instructions'].replace('{{notes_directory}}', str(packet / 'notes') + '/')
    source = folder / 'preparation/INSTRUCTIONS.md'
    keep(source, instructions.encode())
    # Records reserved before connections retain the recipe's complete commission.
    commission = record.get('commission', record['recipe'].get('commission'))
    keep(folder / 'preparation/COMMISSION.md', commission.encode())
    if record.get('connection'):
        template = record['connection']['revision_template']
    else:
        template = store.connection(store.legacy_connection).definition['revision']
    capture_record = record.get('capture') or {'marker': MARKER, 'layout': 'legacy'}
    metadata = record['recipe']['metadata']
    protocol = metadata.get('recording_protocol')
    if protocol is None:
        # Previously reserved recipes retain their bytes; resolve their known identity.
        protocol = {'work-accounts': 'work-accounts/1', 'participant-inquiry': 'participant-inquiry/1'}.get(metadata['id'])
    if protocol is None:
        raise Decision('Reserved recipe needs a recording protocol before preparing its packet')
    if not packet.exists():
        fieldwork.prepare(store.collector_for(record), run, commission, project,
                          template.replace('{head}', context['head']), ['notes'], source, recording_protocol=protocol,
                          marker=capture_record['marker'], layout=capture_record['layout'],
                          connection=(record.get('connection') or {}).get('id'),
                          harness='Claude Code' if record.get('harness', {}).get('id') == 'claude' else 'Codex CLI')
    if read(packet / 'run.json').get('recording_protocol') != protocol:
        raise Decision(f'{run}: packet protocol differs from its recipe; cancel this unused preparation and run berman new')
    # A retry must never replace an existing or worker-edited assignment.
    if (packet / 'INSTRUCTIONS.md').read_bytes() != source.read_bytes():
        raise Decision(f'{run}: instructions differ from the reserved recipe')
    for name in ('INSTRUCTIONS.md', 'ASSIGNMENT.md', 'run.json'):
        keep(folder / 'preparation' / name, files.regular(packet / name).read_bytes())
    record['phase'] = 'prepared'
    store.save(record)
    return packet
