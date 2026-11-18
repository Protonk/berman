"""The operator's cheatsheet: the commands that apply to one study now. It reads and changes nothing.

`berman cheatsheet` and `berman-research cheatsheet` print it. One status reading from the guest
gives every run's recorded facts. Each unfinished engagement is shown with the command that moves it
forward, written out for this installation and study so that it can be pasted. What this host's
spool holds for a run (its packet, a sitting with no recorded exit) is read from files without the
executor's locks. Words in capitals are placeholders: LABEL, REASON, RUN, FILE, DIGEST.

The same value is printed as text or as JSON (`berman.cheatsheet/1`): a heading, notes, and sections
of steps, each step a sentence and the command that does it.
"""

from pathlib import Path
import re
import shlex

from berman import files
from berman.executor import capture
from berman.installation import version

DOCS = 'https://github.com/Protonk/berman/blob/main/docs/'
DEFAULT_CONFIG = '~/.config/berman/connector.json'
# The first collection's facts, in the order the controller records them.
CLOSEOUT = ('ending', 'capture', 'accept', 'preserve', 'publish')
DONE = {
    'ending': 'the ending is recorded',
    'capture': 'the host collection is frozen',
    'accept': 'the guest has accepted it',
    'preserve': 'the recording is preserved',
}


def shown(path):
    """A path as one shell word. A home path keeps its ~ so the command stays short."""
    path = Path(path).expanduser()
    try:
        text = '~/' + path.relative_to(Path.home()).as_posix()
    except ValueError:
        text = str(path)
    return text if re.fullmatch(r'[\w@%+=:,./~-]+', text) else shlex.quote(str(path))


def step(what, *words):
    return {'what': what, 'command': ' '.join(words)}


def staged(spool, operation):
    """What this host holds for a run: (packet path or None, sittings begun here, notes)."""
    folder = capture.operation_dir(spool, operation) if operation else None
    if folder is None or not (folder / 'preparation.json').is_file():
        return None, 0, ['This host’s spool holds no packet for it.']
    notes = []
    numbers = capture.sittings(folder)
    if numbers:
        paths = capture.sitting_files(folder, numbers[-1])
        if not paths['exit'].is_file():
            began = files.read(paths['started'])['observed_at_utc'] if paths['started'].is_file() else None
            notes.append(
                f'Sitting {numbers[-1]} '
                + (f'started at {began}' if began else 'was requested')
                + ' and has no recorded exit on this host. If its session is still open, let it finish first.'
            )
    return folder / 'packet', len(numbers), notes


def engagement(row, run_command, spool, operation):
    """One run's situation and the commands that move it forward.

    `row` is the controller's status overview for the run. `operation` is its assignment's
    operation identity when this host has fetched it, and `spool` is where it would be staged.
    """
    run, facts = row['run'], row['facts']
    late = [s for s in row.get('supplements', []) if 'publish' not in s['facts']]
    packet, begun, notes = staged(spool, operation)
    if 'preparation' not in facts and 'ending' not in facts:
        state, notes = 'commissioned', []
        steps = [
            step('Stage the worker’s packet on this host and run the project checks.', *run_command, 'prepare', run)
        ]
    elif 'launch' not in facts and 'ending' not in facts:
        state = 'prepared'
        steps = [
            step(
                'The first sitting was started on this host and the guest has no report of it. '
                'Once it has ended, repeat this to report it. It starts nothing.'
                if begun
                else 'After the packet is reviewed, the researcher starts the first sitting from an ordinary '
                'terminal. Repeating this never starts a second process.',
                *run_command,
                'launch',
                run,
            )
        ]
    elif 'ending' not in facts:
        state = 'in fieldwork'
        steps = [
            step(
                'Start another sitting of the same engagement. LABEL names that sitting.',
                *run_command,
                'resume',
                run,
                '--request',
                'LABEL',
            ),
            step(
                'When the researcher declares the engagement ended, record their reason and close out.',
                *run_command,
                'closeout',
                run,
                '--reason',
                "'REASON'",
            ),
        ]
    elif 'publish' not in facts:
        state = 'closing'
        reached = [DONE[name] for name in CLOSEOUT if name in facts and name in DONE]
        notes = [*notes, 'So far ' + ', '.join(reached) + '.']
        steps = [step('Repeat closeout. It continues from the recorded step.', *run_command, 'closeout', run)]
    elif late:
        state = 'supplement open'
        notes = []
        steps = [
            step(
                f'Close out supplement {s["sequence"]}: collect, transfer, preserve and publish it.',
                *run_command,
                'closeout',
                run,
                '--supplement',
                str(s['sequence']),
            )
            for s in late
        ]
    else:
        state, notes, packet = 'complete', [], None
        steps = [
            step('Reread its recording and edition in the guest.', *run_command, 'verify', run),
            step(
                'If the worker was active again afterwards, declare a supplement; it adds a second recording.',
                *run_command,
                'supplement',
                run,
                '--request',
                'LABEL',
                '--reason',
                "'REASON'",
            ),
        ]
    sittings = row.get('sittings')
    return {
        'run': run,
        'connection': row['connection'],
        'harness': row.get('harness'),
        'state': state,
        'sittings': sittings,
        'packet': str(packet) if packet and state in ('prepared', 'in fieldwork') else None,
        'notes': notes,
        'steps': steps,
    }


def reading(standalone, where=None):
    notes = [where] if where else []
    return {
        'title': 'Read preserved fieldwork from a copy',
        'notes': notes,
        'steps': [
            step(
                'Verify a recording against its separately kept digest.',
                *standalone,
                'recording',
                'verify',
                'FILE',
                '--sha256',
                'DIGEST',
            ),
            step('Verify a published reading edition.', *standalone, 'archive', '--path', 'FILE'),
        ],
    }


def unconfigured(config, choices, standalone):
    """No connector settings: say so, and show what can be done without a study.

    `choices` holds one cheatsheet command for each settings file found beside the default.
    """
    notes = [f'No connector settings at {shown(config)}, so no study is selected.']
    choose = [step('Settings for a study exist here. Show its cheatsheet.', *words) for words in choices]
    sections = [
        *([{'title': 'Choose a study', 'notes': [], 'steps': choose}] if choose else []),
        {
            'title': 'Check this installation',
            'notes': [],
            'steps': [step('Installed files, dependencies and schemas.', *standalone, 'verify')],
        },
        {
            'title': 'Connect a study',
            'notes': [
                'A study lives in a Linux guest and this host runs the worker’s harness. '
                'The deployment guide sets up both and writes the settings file:',
                DOCS + 'guest-deployment.md',
            ],
            'steps': [],
        },
        reading(standalone),
    ]
    return {
        'schema': 'berman.cheatsheet/1',
        'heading': f'Berman {version()}, no study selected',
        'notes': notes,
        'engagements': None,
        'sections': sections,
    }


def study(settings, run_command, config, rows, problem, operation_of, standalone):
    """The sheet for one configured study.

    `run_command` is the words that reach this study with this installation's operator command.
    `rows` is the controller's status overview, or None with `problem` when the guest did not
    answer. `operation_of(run)` gives the operation identity of a run this host has prepared, or
    None. `standalone` is the words that run this installation's `berman`.
    """
    name, host = settings['study'], settings['host']
    spool = Path(settings['spool']).expanduser()
    notes = [
        f'collection  {settings["collection_id"]}',
        f'settings    {shown(config)}',
        f'staging     {shown(spool)} (on this host)',
    ]
    sections = []
    engagements = []
    if rows is None:
        guest = host.removeprefix('lima-')
        notes += [
            f'The guest ({host}) did not answer: {problem}',
            'Start the guest and repeat'
            + (f' (for a Lima guest: limactl start {guest})' if guest != host else '')
            + '. Hooks keep staging on this host meanwhile; nothing is lost by waiting.',
        ]
    else:
        for row in rows:
            engagements.append(engagement(row, run_command, spool, operation_of(row['run'])))
        if any('supplements' not in row for row in rows):
            notes.append(
                'This guest’s controller does not list supplements in its overview; '
                'an open supplement shows only under status RUN.'
            )
    bindings = [(c, h) for c, harnesses in sorted(settings.get('bindings', {}).items()) for h in sorted(harnesses)]
    sections.append(
        {
            'title': 'Start an engagement',
            'notes': [
                'LABEL names the research decision; reuse it only to retry the same commission. '
                'Add --recipe NAME or --plan NAME, or the connection’s default recipe is used.'
            ],
            'steps': [
                step(
                    f'Commission {harness} fieldwork in {connection}. The reply names the run.',
                    *run_command,
                    'commission',
                    '--request',
                    'LABEL',
                    '--connection',
                    connection,
                    '--harness',
                    harness,
                )
                for connection, harness in bindings
            ],
        }
    )
    sections.append(
        {
            'title': 'Look without changing anything',
            'notes': [],
            'steps': [
                step('Every run and its recorded facts.', *run_command, 'status'),
                step('One run: observed, transferred and outstanding files.', *run_command, 'status', 'RUN'),
            ],
        }
    )
    projects = sorted(
        {(binding['project'], harness) for h in settings.get('bindings', {}).values() for harness, binding in h.items()}
    )
    sections.append(
        {
            'title': 'Ordinary development',
            'notes': ['It needs no Berman command and nothing is recorded.'],
            'steps': [
                step(f'Open {harness} in the checkout.', 'cd', shown(project), '&&', harness)
                for project, harness in projects
            ],
        }
    )
    sections.append(
        reading(
            standalone,
            f'Recordings and editions stay in the guest, under ~/research/{name}/custody/ and '
            f'~/research/{name}/archive/editions/. Closeout prints both paths.',
        )
    )
    return {
        'schema': 'berman.cheatsheet/1',
        'heading': f'Berman {version()}, study {name} in guest {host}',
        'notes': notes,
        'engagements': engagements,
        'sections': sections,
    }


def prepared_note(prepared, run_command):
    """What the operator shows the researcher after `prepare`: the packet and the launch command."""
    packet = Path(prepared['packet'])
    launch = ' '.join([*run_command, 'launch', prepared['run']])
    return (
        f'Packet: {packet}\n'
        f'Review {packet / "ASSIGNMENT.md"} and {packet / "INSTRUCTIONS.md"}.\n'
        f'The researcher then starts the first sitting from an ordinary terminal:\n  {launch}'
    )


def text(sheet):
    lines = [sheet['heading'], *('  ' + note for note in sheet['notes'])]

    def steps(items, indent):
        for item in items:
            lines.append(f'{indent}{item["what"]}')
            lines.append(f'{indent}  {item["command"]}')

    engagements = sheet['engagements'] or []
    open_runs = [e for e in engagements if e['state'] != 'complete']
    complete = [e for e in engagements if e['state'] == 'complete']
    if engagements:
        lines += ['', 'Engagements']
    for entry in open_runs:
        who = ', '.join(part for part in (entry['connection'], entry['harness']) if part)
        count = f', {entry["sittings"]} sitting(s) reported' if entry['sittings'] else ''
        lines.append(f'  {entry["run"]} ({who}): {entry["state"]}{count}')
        if entry['packet']:
            lines.append(f'    Packet: {entry["packet"]}')
        lines += ['    ' + note for note in entry['notes']]
        steps(entry['steps'], '    ')
    if complete:
        latest = complete[-1]
        lines.append(
            f'  {len(complete)} complete: recording preserved and reading edition published. The latest is {latest["run"]}.'
        )
        steps(latest['steps'], '    ')
    for section in sheet['sections']:
        if not section['steps'] and not section['notes']:
            continue
        lines += ['', section['title']]
        lines += ['  ' + note for note in section['notes']]
        steps(section['steps'], '  ')
    repeat = 'Any command can be repeated after an interruption. ' if sheet['engagements'] is not None else ''
    lines += ['', repeat + 'This cheatsheet changed nothing.']
    return '\n'.join(lines)
