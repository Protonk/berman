"""The bound checkout as the executor meets it: declared project checks and bounded document snapshots.

Preparation resolves and runs a connection's checks as host subprocesses in the checkout and records
their arguments, return codes and output digests. Preparation and collection each snapshot the
selected documents and the observed Git state; neither is a copy of the checkout.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from berman import files
from berman.files import Decision, keep, read, write

PLACEHOLDER = re.compile(r'\{(python|project|packet|executable:[a-z][a-z0-9-]*)\}')


def project_checks(declared, values):
    """Resolve a connection's declared checks into explicit argument lists."""
    resolved = []
    seen = set()
    for check in declared:
        ident = check['id']
        if not isinstance(ident, str) or ident in seen or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', ident):
            raise ValueError(f'Invalid project check identity: {ident}')
        seen.add(ident)

        def substitute(match, ident=ident):
            key = match.group(1)
            if key.startswith('executable:'):
                found = shutil.which(key.split(':', 1)[1])
                if not found:
                    raise Decision(f'The native check needs the {key.split(":", 1)[1]} executable for {ident}')
                return found
            return values[key]

        argv = [PLACEHOLDER.sub(substitute, arg) for arg in check['argv']]
        if any(re.search(r'\{[^}]*\}', arg) for arg in argv):
            raise ValueError(f'Unknown placeholder in project check {ident}')
        timeout = int(check.get('timeout', 30))
        if not 0 < timeout <= 120:
            raise ValueError(f'Project check {ident} needs a timeout between 1 and 120 seconds')
        row = {'id': ident, 'argv': argv, 'timeout': timeout}
        if 'stdout_contains' in check:
            row['stdout_contains'] = str(check['stdout_contains'])
        resolved.append(row)
    return resolved


def git_state(project):
    def git(*args):
        p = subprocess.run(
            ['git', '-C', str(project), *args],
            check=True,
            capture_output=True,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'),
        )
        return p.stdout.decode()

    return {
        'head': git('rev-parse', 'HEAD').strip(),
        'status': git('status', '--porcelain=v1', '--untracked-files=normal'),
    }


def selected_documents(project, selectors):
    """Expand bounded, relative selectors at each observation; unmatched selectors stay visible."""
    selected = {}
    for selector in selectors:
        rel = Path(selector)
        if (
            not selector
            or rel.is_absolute()
            or '..' in rel.parts
            or '.git' in rel.parts
            or selector == 'snapshot.json'
            or '**' in selector
        ):
            raise ValueError(f'Invalid selected document: {selector}')
        pattern = any(c in selector for c in '*?[')
        if not pattern and (project / rel).is_dir():
            raise Decision(f'Selected document is a directory: {selector}')
        matches = []
        for path in project.glob(selector) if pattern else [project / rel]:
            if path.is_dir() and not path.is_symlink():
                continue
            matches.append(path)
            if len(matches) > 256:
                raise Decision('Document selector exceeds 256 files; narrow the connection selection')
        matches.sort()
        for path in matches:
            if '.git' in path.relative_to(project).parts:
                raise Decision(f'Selected document is Git metadata: {path}')
            if any(p.is_symlink() for p in (path, *path.parents) if p != project and p.is_relative_to(project)):
                raise Decision(f'Selected document passes through a symlink: {path}')
            selected[path.relative_to(project).as_posix()] = None
        if not matches:
            selected[selector] = None
        if len(selected) > 256:
            raise Decision('Document selectors exceed 256 files; narrow the connection selection')
    return list(selected)


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
        total = 0
        for name in selected_documents(project, names):
            rel = Path(name)
            if rel.is_absolute() or '..' in rel.parts or name == 'snapshot.json':
                raise ValueError(f'Invalid selected document: {name}')
            source = project / rel
            if not source.exists() and not source.is_symlink():
                rows.append({'path': name, 'missing': True})
                continue
            if files.regular(source).stat().st_size > 4 * 1024 * 1024:
                raise Decision(f'Selected document exceeds 4 MiB: {name}')
            data = files.regular(source).read_bytes()
            total += len(data)
            if total > 16 * 1024 * 1024:
                raise Decision('Selected documents exceed 16 MiB; narrow the connection selection')
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
        result = {
            'schema': 'berman.project-snapshot/1',
            'observed_at_utc': files.now(),
            'project': str(project),
            **before,
            'selectors': list(names),
            'files': rows,
            'git_after': after,
            'git_changed_during_collection': before != after,
            'scope': 'Selected documents and observed Git state at collection time; not a full checkout snapshot.',
        }
        write(staging / 'snapshot.json', result)
        staging.rename(target)
        return result
    finally:
        if staging.exists():
            shutil.rmtree(staging)
