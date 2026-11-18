"""The established plain-text renderings. Scripts and saved output depend on these exact bytes.

Pinned by the fixtures in tests/fixtures/presentation. Change a line here only as a deliberate,
documented change to Berman's output.
"""

LABELS = {'passed': 'passed', 'failed': 'FAILED', 'not-established': 'not established', 'not-run': 'not run'}
# Detail lists a check may carry, and the word each item is introduced with.
LISTED = {
    'failing': '',
    'problems': '',
    'unsettled': '',
    'mismatched': 'mismatched: ',
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
    if result['record']:
        lines.append('Recorded in ' + result['record']['directory'])
    return '\n'.join(lines)


def unknown_reasons(row):
    """(fact, reason) for each unknown fact's reasons that the row's problems do not already state."""
    shown = set(row['problems'])
    for name in row['unknown']:
        for reason in row['lifecycle'][name]['unknown']:
            if reason not in shown:
                shown.add(reason)
                yield name, reason


def plan(row):
    """One line for a connection's selected plan, as its records left it."""
    if row['plan'] is None and not row.get('plan_error'):
        return 'no research plan selected'
    name = f'plan {row["plan"]}' if row['plan'] else 'selected plan'
    if row.get('plan_error'):
        return f'{name} needs attention: {row["plan_error"]}'
    if row['next_step'] is None:
        return f'{name}: {row["plan_note"]}'
    return f'{name}: first unstarted step is {row["next_step"]} (recipe {row["next_recipe"]})'


def request(row):
    return f'pending combined request: {row["path"]} (end {row["previous_run"]}, prepare {row["result_run"]})'


def trial(row):
    return f'{row["run"]} (support trial, {row["adapter"]}; not research): {row["phase"] or "phase not recorded"}'


def status(result):
    lines = [f'{result["archived"]} archived runs. Default connection: {result["default_connection"]}']
    for problem in result['workspace_problems']:
        lines.append('Workspace needs attention: ' + problem)
    for name, row in result['connections'].items():
        lines.append(f'{name}: {plan(row)}')
        for problem in row['problems']:
            lines.append('  needs attention: ' + problem)
    for row in result['pending_requests']:
        lines.append(request(row))
    if not result['runs']:
        lines.append('No outstanding runs.')
    for row in result['runs']:
        lines.append(f'{row["run"]} ({row["connection"]}): {row["summary"]}')
        lines.append('  ' + assignment(row))
        if row['last_capture']:
            lines.append(f'  Last capture at {row["last_capture"]}')
        lines += [f'  {name} unknown: {reason}' for name, reason in unknown_reasons(row)]
        for line in row['lifecycle']['disagreements'] + row['problems']:
            lines.append('  ' + line)
    for row in result['support_trials']:
        lines.append(trial(row))
        for problem in row['problems']:
            lines.append('  ' + problem)
    if result['unsettled']:
        lines.append('Unsettled:')
        lines += ['  ' + item for item in result['unsettled']]
    lines.append(result['meaning'])
    return '\n'.join(lines)


def assignment(row):
    """Expose retained assignment facts without inferring missing historical origin."""
    return (
        f'Assignment: {row.get("recipe") or "not recorded"}; origin: {row.get("assignment_origin") or "not recorded"}'
    )
