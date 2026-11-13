"""Packets as the removed legacy-codex adapter prepared them.

The current collectors are built on the frozen capture_session.py, whose own packet layout keeps
narrative.md and scratch/ placeholders. No product code prepares that layout any more; this builder
keeps it available so the frozen collector's capture, finalization, supplement and migration
behavior stays exercised directly. It is the preparation function as it stood in 0.3.0.
"""
from pathlib import Path
import shutil
import tempfile

from berman import files
from berman._legacy import capture_session as capture
from berman.recovery import SUPPORT_CONNECTION, SUPPORT_PROTOCOL


def frozen_packet(collector, run, assignment, manager_cwd, revision, coverage, instructions_from=None, recording_protocol=None,
            marker=None, layout='legacy', connection=None, harness='Codex CLI'):
    """The legacy layout keeps narrative.md and scratch/ placeholders for the frozen collector.

    The notes layout omits them, so the frozen collector cannot register such a packet.
    """
    if not capture.RUN.fullmatch(run):
        raise ValueError('Run must have 3 to 6 digits')
    if not assignment.strip() or not revision.strip():
        raise ValueError('Assignment and revision are required; use an explicit description such as not-applicable for a support trial')
    files.safe_directory(manager_cwd)
    notes_only = set(coverage) == {'notes'}
    if recording_protocol is not None and (not notes_only or recording_protocol not in ('work-accounts/1', 'participant-inquiry/1', SUPPORT_PROTOCOL)):
        raise ValueError('The selected recording protocol requires notes coverage and a supported protocol identity')
    if recording_protocol == SUPPORT_PROTOCOL and connection != SUPPORT_CONNECTION:
        raise ValueError(f'{SUPPORT_PROTOCOL} is reserved for native support-trial packets')
    allowed = {'narrative', 'scratch', 'git-log', 'auditor-streams', 'narrative-timestamps', 'full-command-outputs'}
    if not notes_only and (not {'narrative', 'scratch'} <= set(coverage) or set(coverage) - allowed):
        raise ValueError('Coverage must be notes alone, or include narrative,scratch with optional git-log,auditor-streams,narrative-timestamps,full-command-outputs')
    if notes_only and not instructions_from:
        raise ValueError('Notes coverage requires explicit researcher-authored --instructions-from')
    if layout not in ('legacy', 'notes') or (layout == 'notes' and (not notes_only or not marker or not connection)):
        raise ValueError('The notes layout needs notes coverage, its collector marker, and a connection identity')
    collector.fieldwork.mkdir(parents=True, exist_ok=True)
    files.safe_directory(collector.fieldwork)
    root = collector.fieldwork / run
    with collector.locked():
        if root.exists() or root.is_symlink() or collector.archived(run):
            raise ValueError('Run number or destination already exists; preparation never overwrites a packet')
        source = None
        if instructions_from:
            source = files.regular(Path(instructions_from))
            text = source.read_text()
            # Adapt explicit recording locations without executing the source.
            import re
            text = re.sub(r'(?:~|/)[^`\n]*?/run-[0-9]{3,6}(?=/(?:narrative\.md|scratch(?:/|`)|notes(?:/|`)|git-commands\.log))', lambda _: str(root), text)
            expected = ('notes',) if notes_only else ('narrative.md', 'scratch')
            if any(str(root / name) not in text for name in expected):
                raise ValueError('Could not identify the recording paths in the supplied instructions; supply a source with explicit paths')
            if notes_only and ('narrative.md' in text or str(root / 'scratch') in text):
                raise ValueError('Notes instructions must not carry the old narrative or scratch destinations')
            if 'git-commands.log' in text and 'git-log' not in coverage:
                raise ValueError('Source instructions require a Git log but coverage does not; choose compatible instructions or coverage')
        else:
            text = (f'# Stewart Fieldwork — Recording Instructions\n\nMaintain a running narrative in `{root}/narrative.md`.\n'
                    'In ordinary prose, record what you did, what happened, and what you did next. Update it periodically.\n\n'
                    f'Keep scratch probes, temporary code, and saved outputs under `{root}/scratch/`.\n'
                    'Organize them as convenient.\n')
        additions = {
            'git-log': f'Append direct Git invocations to `{root}/git-commands.log` with recorded times.',
            'auditor-streams': 'Save each available auditor stream separately under scratch/ with a filename beginning auditor and ending .jsonl; subdirectories are supported. Record any unavailable stream.',
            'narrative-timestamps': 'Give narrative entries their recorded UTC time on a separate line in YYYY-MM-DDTHH:MM:SSZ form; do not infer missing times.',
            'full-command-outputs': 'Save full stdout/stderr for commands whose outputs would otherwise be truncated; note anything unavailable.'}
        extra = [additions[key] for key in coverage if key in additions]
        if extra:
            text += '\n## Additional requested coverage\n\n' + '\n\n'.join(extra) + '\n'
        opening = ((marker or capture.MARKER) + run + '\n\n'
                   f'Your working directory is `{manager_cwd}`. The revision or study target is: {revision}.\n'
                   f'Read `{root}/INSTRUCTIONS.md` before beginning and follow its recording requirements.\n\n'
                   + assignment.rstrip() + '\n')
        metadata = {'schema': 'stewart.run-preparation/1', 'run': run, 'prepared_at_utc': files.now(),
                    'manager': {'cwd': str(manager_cwd), 'revision': revision, 'harness': harness,
                                'model': 'selected by operator; not set by this packet'},
                    'coverage': coverage, 'instructions_from': str(source) if source else None,
                    'source_instructions_sha256': files.digest(source.read_bytes()) if source else None,
                    'assignment_sha256': files.digest(assignment.encode())}
        if notes_only:
            metadata['recording_protocol'] = recording_protocol or 'work-accounts/1'
            metadata['collector_layout'] = {
                'notes/': 'Requested participant accounts and selected supporting copies.',
                'narrative.md': 'Empty collector-layout placeholder; no narrative requested.',
                'scratch/': 'Collector-layout placeholder; no general scratch collection requested.'}
        if layout == 'notes':
            metadata['schema'] = 'berman.run-preparation/1'
            metadata.update(connection=connection, capture_marker=marker.strip())
            metadata['collector_layout'] = {'notes/': 'Requested participant accounts and selected supporting copies.'}
        staging = Path(tempfile.mkdtemp(prefix='.' + run + '-prepare-', dir=collector.fieldwork))
        try:
            if layout == 'legacy':
                (staging / 'scratch').mkdir()
                (staging / 'narrative.md').write_text('')
            if notes_only:
                (staging / 'notes').mkdir()
            (staging / 'INSTRUCTIONS.md').write_text(text)
            (staging / 'ASSIGNMENT.md').write_text(opening)
            (staging / 'run.json').write_bytes(files.json_bytes(metadata))
            if 'git-log' in coverage:
                (staging / 'git-commands.log').write_text('')
            staging.rename(root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {'state': 'prepared', 'path': str(root), 'assignment': str(root / 'ASSIGNMENT.md'),
            'manager_cwd': str(manager_cwd), 'coverage': coverage,
            'next': f'Use ASSIGNMENT.md in the prepared {harness} session; inspect status after its first response'}
