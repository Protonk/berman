"""A connection's declared project checks, resolved into explicit argument lists.

The executor runs each resolved check as a host subprocess in the checkout while it prepares an
assignment, and its preparation receipt records the arguments, return code and output digests.
"""

import re
import shutil

from berman.files import Decision

# Identities the co-located native probe reported in its receipts. A connection's own check may
# not take one, so a recorded name keeps meaning one thing.
RESERVED = frozenset(
    {
        'project_read',
        'workspace_write',
        'cat_positive_control',
        'rg_positive_control',
        'git_read',
        'git_disposable_commit',
        'assigned_instructions_readable',
        'assigned_notes_writable',
        'shell_exit_zero',
        'launch_policy_observed',
        'configuration_unchanged',
        'file_project_read',
        'file_packet_read',
        'exact_execution_probe_observed',
    }
)
PLACEHOLDER = re.compile(r'\{(python|project|fixture|packet|executable:[a-z][a-z0-9-]*)\}')


def project_checks(declared, values):
    """Resolve a connection's declared checks into explicit argument lists."""
    resolved = []
    reserved = set(RESERVED)
    for check in declared:
        ident = check['id']
        if ident in reserved or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', ident):
            raise ValueError(f'Invalid project check identity: {ident}')
        reserved.add(ident)

        def substitute(match, ident=ident):
            key = match.group(1)
            if key.startswith('executable:'):
                found = shutil.which(key.split(':', 1)[1])
                if not found:
                    raise Decision(f'The native check needs the {key.split(":", 1)[1]} executable for {ident}')
                return found
            return values[key]

        argv = [PLACEHOLDER.sub(substitute, str(arg)) for arg in check['argv']]
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
