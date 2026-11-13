#!/usr/bin/env python3
"""Per-launch Claude hooks: research read boundaries and recording, never model continuation."""
import argparse
import json
import os
from pathlib import Path
import sys
import unicodedata

from berman import files
from berman.capture.claude import ClaudeCollector
from berman._legacy.api import RUN


def canonical(path):
    # Conservatively cover case/Unicode aliases on the common macOS filesystem.
    return unicodedata.normalize('NFD', str(Path(path).resolve())).casefold()


def within(path, root):
    return path == root or path.startswith(root.rstrip('/') + '/')


def access(event, root, record):
    """Deny only research access; ordinary permissions still decide everything else.

    Recursive searches of an ancestor are refused, rather than silently returning research
    contents. Bash uses the OS sandbox. A file-tool rule cannot carve a child out of an absolute
    parent deny, so this hook implements that exception for built-in tools explicitly.
    """
    name = event.get('tool_name')
    fields = {'Read': 'file_path', 'Edit': 'file_path', 'Write': 'file_path',
              'NotebookEdit': 'notebook_path', 'Grep': 'path', 'Glob': 'path'}
    if name not in fields:
        return {}
    args = event.get('tool_input', {})
    if not isinstance(args, dict):
        raise ValueError('Invalid file-tool input')
    cwd = Path(event.get('cwd') or record['project'])
    value = args.get(fields[name])
    if value is None and name in ('Grep', 'Glob'):
        value = str(cwd)
    if not isinstance(value, str) or not value:
        raise ValueError('File-tool path is missing')
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = cwd / target
    paths = [canonical(target)]
    if name == 'Glob':
        pattern = args.get('pattern', '')
        if not isinstance(pattern, str):
            raise ValueError('Invalid glob pattern')
        # Absolute patterns and parent traversals can name somewhere beyond path.
        base = Path(pattern).expanduser()
        literal = []
        for part in base.parts:
            if any(char in part for char in '*?['):
                break
            literal.append(part)
        prefix = Path(*literal) if literal else Path('.')
        paths.append(canonical(prefix if prefix.is_absolute() else target / prefix))
    research, field, packet = canonical(root), canonical(record['packet_root']), canonical(record['packet'])
    reason = None
    for path in paths:
        if within(path, research) or (within(path, field) and not within(path, packet)):
            reason = 'Berman research files and other packets are outside this assignment.'
        if name in ('Glob', 'Grep') and (within(research, path) or within(field, path)):
            reason = 'Search a specific development or assigned-packet directory, not an ancestor of research storage.'
    if reason:
        return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                      'permissionDecisionReason': reason}}
    return {}


def route(mode, event, root, run):
    if not RUN.fullmatch(run):
        raise ValueError('Invalid run identity')
    record = files.read(root / 'operations' / run / 'operation.json')
    if record.get('run') != run or record.get('capture', {}).get('adapter') != 'berman-claude':
        raise ValueError('Hook is not assigned to this Claude run')
    if mode == 'access':
        return access(event, root, record)
    # Subagents may inherit settings. Only the explicitly reserved parent is captured.
    if event.get('session_id') != record['harness']['session_id']:
        return {}
    collector = ClaudeCollector(Path(record['packet']).parent, record['harness']['home'], root).for_run(run)
    return collector.hook(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('capture', 'access'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin)
        result = route(args.mode, event, args.root, args.run)
    except Exception as exc:
        if args.mode == 'access':
            result = {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                                            'permissionDecisionReason': f'Berman access check failed: {exc}'}}
        else:
            # A capture failure is reported, never a reason to manufacture another model turn.
            print(f'Berman Claude capture failed: {exc}', file=sys.stderr)
            result = {'systemMessage': f'Berman Claude capture failed: {exc}'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
