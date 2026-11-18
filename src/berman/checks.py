"""A connection's declared project checks, resolved into explicit argument lists.

The executor runs each resolved check as a host subprocess in the checkout while it prepares an
assignment, and its preparation receipt records the arguments, return code and output digests.
"""

import re
import shutil

from berman.files import Decision

PLACEHOLDER = re.compile(r'\{(python|project|fixture|packet|executable:[a-z][a-z0-9-]*)\}')


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
