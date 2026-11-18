"""The established plain-text renderings. Scripts and saved output depend on these exact bytes.

Pinned by the fixtures in tests/fixtures/presentation. Change a line here only as a deliberate,
documented change to Berman's output.
"""

LABELS = {'passed': 'passed', 'failed': 'FAILED', 'not-established': 'not established', 'not-run': 'not run'}
# Detail lists a check may carry, and the word each item is introduced with.
LISTED = {
    'problems': '',
    'missing': 'missing: ',
}
SHOWN = 10  # Items of one detail list that are printed; the rest are counted.


def listed(check):
    """(prefix, item) for each detail a check lists, then a count of those left out, as (None, text)."""
    for key, prefix in LISTED.items():
        items = check['details'].get(key) or []
        for item in items[:SHOWN]:
            yield prefix, item
        if len(items) > SHOWN:
            yield None, f'... and {len(items) - SHOWN} more'


def named(result, names):
    return ', '.join(n + ('' if result['checks'][n]['required'] else ' (optional)') for n in names)


def verification(result):
    lines = [f'Berman verification: {result["verdict"].upper()}']
    for name, check in result['checks'].items():
        label = LABELS[check['status']] + ('' if check['required'] else ' (optional)')
        lines.append(f'  {label:<28}{name}: {check["summary"]}')
        lines += [' ' * 30 + (prefix or '') + item for prefix, item in listed(check)]
        if check.get('next'):
            lines.append(' ' * 30 + 'next: ' + check['next'])
    for key, title in (('failed', 'Failed'), ('not_run', 'Not run'), ('not_established', 'Not established')):
        if result[key]:
            lines.append(f'{title}: {named(result, result[key])}')
    lines.append('Never checked by berman verify:')
    lines += ['  ' + item for item in result['not_covered']]
    return '\n'.join(lines)
