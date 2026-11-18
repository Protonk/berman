"""The established plain-text renderings. Scripts and saved output depend on these exact bytes.

Moved here unchanged from the command line and the verification module, and pinned by the
fixtures in tests/fixtures/presentation. Change a line here only as a deliberate, documented
change to Berman's output.
"""

LABELS = {'passed': 'passed', 'failed': 'FAILED', 'not-established': 'not established', 'not-run': 'not run'}
# Detail lists a check may carry, and the word each item is introduced with.
LISTED = {
    'failing': '',
    'problems': '',
    'mismatched': 'mismatched: ',
    'missing': 'missing: ',
    'changed_implementation': 'changed: ',
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


def cheatsheet(result):
    """Commands a person types now, grouped by connection; the JSON form carries the same content."""
    lines = []
    for problem in result['workspace_problems']:
        lines.append('Workspace needs attention: ' + problem)
    for conn in result['connections']:
        title = f'{conn["name"]} ({conn["id"]}' + (', default connection)' if conn['default'] else ')')
        lines += [title, '  checkout: ' + conn['checkout'], '']
        lines.append('  Ordinary development (no fieldwork record, no research-plan step; works from any directory):')
        if not conn['development']:
            lines.append('    use the native harness directly from the checkout')
        for dev in conn['development']:
            note = '' if dev['available'] else '    (harness missing or not executable: ' + dev['launcher'] + ')'
            lines.append('    ' + dev['command'] + note)
        if conn['development']:
            lines.append('    Pass native harness arguments after --. Use --check to print its version.')
        lines.append('')
        commands, fieldwork = conn['commands'], conn['fieldwork']
        recipe_choice = not conn['next_recipe'] and not (
            fieldwork['ready'] or fieldwork['incomplete'] or conn.get('pending_requests')
        )
        lines.append('  Fieldwork:')
        if conn.get('plan_error'):
            lines.append('    plan needs attention: ' + conn['plan_error'])
        for path in conn.get('pending_requests', []):
            lines.append('    pending combined request: ' + path)
        if conn.get('pending_requests'):
            lines.append('    resume the recorded request with:')
            lines.append('      ' + commands['new'])
        if conn['operation_in_progress']:
            lines.append('    an operation is in progress on this connection; wait for it, then ' + commands['status'])
        for item in fieldwork['attention']:
            lines.append(f'    {item["run"]} needs attention: {item["problem"]}; see ' + commands['status'])
        for run, command in zip(fieldwork['ready'], commands['launch'], strict=True):
            lines.append(f'    {run} is prepared and unstarted; start it with:')
            lines.append('      ' + command)
        for run in fieldwork['incomplete']:
            lines.append(f'    {run} has an incomplete preparation; complete it with:')
            lines.append('      ' + commands['new'])
        for run, finish, supplement in zip(fieldwork['open'], commands['finish'], commands['supplement'], strict=True):
            if conn.get('pending_requests'):
                lines.append(f'    {run} is an open engagement; resume the recorded request before another choice.')
            else:
                lines.append(f'    {run} is an open engagement. When the researcher says it has ended:')
                lines.append('      ' + finish)
                lines.append(
                    '    or end it while preparing the next fieldworker'
                    + (' (choose a recipe; replace RECIPE):' if recipe_choice else ':')
                )
                lines.append('      ' + commands['finish_previous'])
                lines.append(
                    '    or keep it open while preparing another'
                    + (' (choose a recipe; replace RECIPE):' if recipe_choice else ':')
                )
                lines.append('      ' + commands['keep_open'])
            lines.append('    retain later activity without ending it:')
            lines.append('      ' + supplement)
        for run in fieldwork['ending']:
            lines.append(f'    {run} has a declared ending in progress; ' + commands['status'] + ' shows its state')
        if not any(fieldwork.values()) and not conn.get('pending_requests'):
            if conn['next_recipe']:
                lines.append(
                    f'    nothing outstanding; the next agreed role is {conn["next_recipe"]}. Prepare it with:'
                )
                lines.append('      ' + commands['new'])
                lines.append('    then start it with the launch command that new prints')
            else:
                lines.append('    nothing outstanding; ' + (conn['next_note'] or 'no next role'))
        elif conn['next_recipe'] and not fieldwork['ready'] and not fieldwork['incomplete']:
            lines.append(f'    the next agreed role after these is {conn["next_recipe"]}')
        if commands.get('new_direct') and not (
            fieldwork['ready'] or fieldwork['incomplete'] or conn.get('pending_requests')
        ):
            lines.append('    Direct assignment template (choose a recipe; replace RECIPE):')
            lines.append('      ' + commands['new_direct'])
        lines.append('')
    lines.append(result['meaning'])
    return '\n'.join(lines)


def unknown_reasons(row):
    """(fact, reason) for each unknown fact's reasons that the row's problems do not already state."""
    shown = set(row['problems'])
    for name in row['unknown']:
        for reason in row['lifecycle'][name]['unknown']:
            if reason not in shown:
                shown.add(reason)
                yield name, reason


def status(result):
    lines = [f'{result["archived"]} archived runs. Default connection: {result["default_connection"]}']
    for problem in result['workspace_problems']:
        lines.append('Workspace needs attention: ' + problem)
    for name, row in result['connections'].items():
        busy = '; an operation is in progress' if row['operation_in_progress'] else ''
        lines.append(f'{name}: next planned fieldwork role: {row["next_recipe"] or "none"}{busy}')
        if row.get('plan_error'):
            lines.append('  plan needs attention: ' + row['plan_error'])
        if not row['next_recipe'] and row.get('next_note'):
            lines.append('  ' + row['next_note'])
        for problem in row['blocked_by']:
            lines.append('  needs attention: ' + problem)
        for path in row['pending_requests']:
            lines.append('  pending combined request: ' + path)
    if not result['runs']:
        lines.append('No outstanding engagements or preparations.')
    for row in result['runs']:
        lines.append(f'{row["run"]} ({row["connection"]}): {row["summary"]}')
        lines.append('  ' + assignment(row))
        if row['last_capture']:
            lines.append(f'  Capture at {row["last_capture"]}; consistent: {row["capture_consistent"]}')
        lines += [f'  {name} unknown: {reason}' for name, reason in unknown_reasons(row)]
        for line in row['lifecycle']['disagreements'] + row['warnings'] + row['problems']:
            lines.append('  ' + line)
    for trial in result['support_trials']:
        lines.append(
            f'{trial["run"]} (support trial, {trial["adapter"]}; not research): {trial["phase"]}; '
            f'capture: {trial["capture_state"]}; next: {trial["next"]}'
        )
        for problem in trial['problems']:
            lines.append('  ' + problem)
    return '\n'.join(lines)


def prepared(result):
    """The result of berman new: what was preserved, the run made ready and how to launch it."""
    lines = [f'Preserved {row["run"]}: {row["package"]["path"]}' for row in result['preserved']]
    checks = (
        'Prior native checks reused; configuration unchanged.'
        if result['checks_reused']
        else 'Native checks passed in this preparation.'
    )
    lines.append(f'{result["run"]} ({result["connection"]}) ready using {result["recipe"]}. {checks}')
    lines.append(assignment(result))
    lines += ['Warning: ' + warning for warning in result.get('warnings', [])]
    lines.append('Launch when ready:\n\n' + result['launch'])
    return '\n'.join(lines)


def assignment(row):
    """Expose retained assignment facts without inferring missing historical origin."""
    return (
        f'Assignment: {row.get("recipe") or "not recorded"}; origin: {row.get("assignment_origin") or "not recorded"}'
    )


def finished(result):
    return (
        f'{result["run"]} preserved; reading edition published and verified.\n'
        f'Recording: {result["package"]["path"]}\n'
        f'Reading: {result["reading"]["archive_path"]}\n'
        'Ready for a new fieldworker or ordinary developer session.'
    )


def ready(result):
    return f'{result["connection"]}: {result["state"]}.\n{result["scope"]}\nNext: {result["next"]}'
