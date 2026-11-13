"""Distinct lifecycle facts for a run, each with its evidence or the reason it is unknown.

Facts never imply one another. A Stop or SessionEnd callback leaves a capture open; finalized.json
or ended.json closes it; finalization freezes bytes; only an operator declaration ends an
engagement; migration moves and indexes a packet; packaging writes its recording package. A value
of None means the evidence a fact needs is damaged or missing: it never counts as False, and the
fact's `unknown` says why. This module reads no files; discovery gathers evidence and passes it in.
"""
from pathlib import Path

from berman._legacy.api import RUN, SESSION

PHASES = ('reserved', 'prepared', 'ready', 'launching', 'active', 'closing', 'archived', 'cancelling', 'cancelled')
BEFORE_SESSION = PHASES[:4]
CANCELLATION = ('cancelling', 'cancelled')
JOURNAL_PHASES = ('inventoried', 'moved-and-verified', 'indexed')
FACTS = ('registered', 'capture', 'finalized', 'ending', 'migration', 'packaged')
SESSION_MATERIAL = ('session', 'session-history', 'session-supplements')
CLOSURE = {'finalized.json': 'frozen', 'ended.json': 'registration moved to another run'}
INFERRED_ENDING = 'Previously written finalized.json or migration journal; no new completion inferred.'
ENDING_MEANING = 'Operator declares this engagement ended; not a claim that its development or inquiry is complete.'
LOW_LEVEL = 'migrated without a declared ending (low-level migration); no recording package is made automatically'
# Collector events that are not callbacks; a finalization attempt still open did not freeze anything.
MANUAL_EVENTS = {'manual-finalize': 'a finalization attempt that did not freeze it', 'manual-refresh': 'a manual refresh',
                 'manual-adopt': 'manual adoption'}
# The workflow writes each of these phases only together with one of the named fields.
REQUIRES = {'launching': ('launch_attempts',), 'active': ('started_session',), 'closing': ('ending', 'migration_authority'),
            'archived': ('package',), 'cancelling': ('cancellation',), 'cancelled': ('cancellation',)}


def fact(value, evidence=(), unknown=(), **detail):
    """A fact's value and evidence, and why it is unknown when the value is None."""
    result = {'value': value, 'evidence': list(evidence), **detail}
    if value is None:
        result['unknown'] = list(unknown) or ['no readable evidence']
    return result


def declared(record):
    """Only an operator declaration ends an engagement; an earlier inferred interpretation does not."""
    ending = (record or {}).get('ending')
    return bool(ending and ending.get('basis') != INFERRED_ENDING)


def declaration(reason, allow_incomplete, at_utc):
    return {'declared_at_utc': at_utc, 'reason': reason, 'allow_incomplete': allow_incomplete, 'meaning': ENDING_MEANING}


def validate_record(value, run):
    """The record unchanged, or ValueError naming each shape the workflow never writes."""
    if not isinstance(value, dict) or value.get('schema') != 'berman.operation/1' or value.get('run') != run:
        raise ValueError(f'not a berman.operation/1 record for {run}')
    purpose = value.get('purpose')
    rules = PURPOSES.get(purpose) if purpose is None or isinstance(purpose, str) else None
    problems = rules(value) if rules else [f'unknown record purpose {purpose!r}']
    if problems:
        raise ValueError('; '.join(problems))
    return value


def research(value):
    """Problems in a research engagement's record."""
    problems = []
    phase = value.get('phase')
    if phase not in PHASES:
        problems.append(f'unknown phase {phase!r}')
    elif phase in REQUIRES and not any(value.get(key) for key in REQUIRES[phase]):
        problems.append(f'phase {phase} needs {" or ".join(REQUIRES[phase])}')
    ending = value.get('ending')
    if ending is not None:
        if not isinstance(ending, dict) or not isinstance(ending.get('reason'), str) or not ending['reason'].strip():
            problems.append('ending needs a reason')
        else:
            if declared(value) and not isinstance(ending.get('declared_at_utc'), str):
                problems.append('a declared ending needs declared_at_utc')
            # An earlier workflow stored the explanation verbatim, including a blank one.
            if not isinstance(ending.get('allow_incomplete'), (str, type(None))):
                problems.append('allow_incomplete must be absent, null or text')
    package = value.get('package')
    if package is not None and not (isinstance(package, dict) and isinstance(package.get('path'), str)):
        problems.append('package needs a path')
    session = value.get('started_session')
    if session is not None and not (isinstance(session, str) and SESSION.fullmatch(session)):
        problems.append('started_session is not a session identity')
    retained = value.get('retained_open_runs', [])
    if not isinstance(retained, list) or any(not isinstance(r, str) or not RUN.fullmatch(r) for r in retained):
        problems.append('retained_open_runs must list run identities')
    return problems


def support(value):
    """Problems in a support trial's reservation marker; berman_support writes it once and never changes it."""
    def field(*keys):
        found = value
        for key in keys:
            found = found.get(key) if isinstance(found, dict) else None
        return found if isinstance(found, str) and found else None
    problems = [] if value.get('phase') == 'support-trial' else [f'unknown support phase {value.get("phase")!r}']
    required = {'workspace.root': ('workspace', 'root'), 'packet': ('packet',), 'packet_root': ('packet_root',),
                'capture.adapter': ('capture', 'adapter'), 'harness.home': ('harness', 'home'),
                'support_trial': ('support_trial',)}
    problems += [f'{name} is missing' for name, keys in required.items() if field(*keys) is None]
    if field('packet') and field('packet_root') and Path(value['packet']) != Path(value['packet_root']) / value['run']:
        problems.append('packet must be the run directly within packet_root')
    trial = field('support_trial')
    parts = Path(trial).parts if trial else ()
    if trial and (Path(trial).is_absolute() or '..' in parts or len(parts) != 3 or parts[0] != 'experiments'
                  or parts[-1] != 'trial.json'):
        problems.append('support_trial must name experiments/NAME/trial.json')
    return problems


# A record's purpose selects its rules; research records carry none.
PURPOSES = {None: research, 'support-trial': support}


def validate_request(value):
    """A combined new-run request unchanged, or ValueError naming its problems."""
    if not isinstance(value, dict) or value.get('schema') != 'berman.new-request/1':
        raise ValueError('not a berman.new-request/1 record')
    problems = []
    if value.get('state') not in ('pending', 'complete'):
        problems.append(f'unknown state {value.get("state")!r}')
    for key in ('previous_run', 'result_run'):
        run = value.get(key)
        if (run is not None or key == 'previous_run') and not (isinstance(run, str) and RUN.fullmatch(run)):
            problems.append(f'{key} is not a run identity')
    for key in ('finish_reason', 'step'):
        if not isinstance(value.get(key), str) or not value[key]:
            problems.append(f'{key} is missing')
    if problems:
        raise ValueError('; '.join(problems))
    return value


def journal_phase(value, run):
    """A migration journal's phase. Destinations are not compared: run-006 names a pre-rename root."""
    if not isinstance(value, dict) or value.get('schema') != 'stewart.migration/1':
        raise ValueError('not a stewart.migration/1 journal')
    if value.get('run') not in (None, run) or not isinstance(value.get('entry'), dict) or value['entry'].get('run') != run:
        raise ValueError('Journal names another run')
    if value.get('phase') not in JOURNAL_PHASES:
        raise ValueError(f'unknown migration phase {value.get("phase")!r}')
    return value['phase']


def assess(record, evidence):
    """Every fact for one run from what discovery observed.

    evidence: location, placed (where presence evidence was read: the location, or for a problem
    row its single packet root's location, else None), record_error, binding, registrations
    [{path, retired}], replacements, closed_by, session_material, capture (collector status of an
    active packet), journal (migration journal phase, or 'damaged'), indexed, problems.
    """
    registered = registration(record, evidence)
    capture = capture_state(evidence, registered['value'])
    lifecycle = {'registered': registered, 'capture': capture, 'finalized': finalization(evidence, capture),
                 'ending': recorded(record, evidence, ending,
                                    'no operation record; preserved before Berman recorded ending declarations'),
                 'migration': migration(evidence),
                 'packaged': recorded(record, evidence, packaged,
                                      'no operation record; preserved before Berman made recording packages'),
                 'disagreements': disagreements(record, evidence, registered['value'])}
    lifecycle['unknown'] = [name for name in FACTS if lifecycle[name]['value'] is None]
    return lifecycle


def registration(record, evidence):
    found = []
    if evidence['binding']:
        found.append(f'session/binding.json names {evidence["binding"].get("session_id")}')
    if (record or {}).get('started_session'):
        found.append('operation record started_session ' + record['started_session'])
    found += ['registration ' + r['path'] + (' (retired)' if r['retired'] else '') for r in evidence['registrations']]
    found += ['replacement receipt ' + path for path in evidence['replacements']]
    if found:
        return fact(True, found)
    reasons = list(evidence['problems'])
    if evidence['record_error'] and evidence['record_error'] not in reasons:
        reasons.append('operation record unreadable: ' + evidence['record_error'])
    if reasons:
        return fact(None, unknown=reasons)
    return fact(False, ['no session binding, registration, replacement receipt or recorded session'])


def capture_state(evidence, registered):
    status = evidence['capture'] or {}
    closed = evidence['closed_by']
    detail = {'closed_by': list(closed), 'last_event': (status.get('capture') or {}).get('event'),
              'consistent': status.get('snapshot_consistent'), 'turn_pending': status.get('prompt_without_terminal_callback')}
    if closed:
        return fact('closed', ['session/' + name for name in closed], **detail)
    if registered is False:
        return fact('none', ['no registered session'], **detail)
    if registered is None or evidence['problems']:
        return fact(None, unknown=list(evidence['problems']) or ['registration unknown'], **detail)
    if evidence['binding'] is None and evidence['replacements']:
        return fact('displaced', evidence['replacements'], **detail)
    if evidence['binding'] is None:
        return fact(None, unknown=['a session is recorded but the packet has no session binding'], **detail)
    if evidence['location'] != 'active':
        return fact(None, unknown=[f'{evidence["location"]} packet has neither session/finalized.json nor session/ended.json'],
                    **detail)
    last = detail['last_event']
    return fact('open', ['session/binding.json', f'last capture event {last}' if last else 'no snapshot yet'], **detail)


def finalization(evidence, capture):
    if 'finalized.json' in evidence['closed_by']:
        return fact(True, ['session/finalized.json'])
    if capture['value'] is None:
        return fact(None, unknown=capture['unknown'])
    return fact(False, ['no session/finalized.json'])


def recorded(record, evidence, build, missing):
    """A fact only the operation record holds: unknown if it is unreadable, or absent unless the packet is active."""
    if evidence['record_error']:
        return fact(None, unknown=['operation record unreadable: ' + evidence['record_error']])
    if record is None and evidence['placed'] in ('archived', None):
        return fact(None, unknown=[missing] if evidence['placed'] else evidence['problems'])
    return build(record or {})


def ending(record):
    value = record.get('ending')
    if declared(record):
        return fact(True, [f'declared {value.get("declared_at_utc")}: {value["reason"]}'],
                    allow_incomplete=value.get('allow_incomplete'))
    notes = ['earlier inferred interpretation retained; not a declaration'] if value else []
    if record.get('previous_ending_interpretations'):
        notes.append('previous inferred interpretations retained; not declarations')
    return fact(False, notes or ['no ending declaration recorded'])


def packaged(record):
    package = record.get('package')
    return fact(True, [package['path']]) if package else fact(False, ['no recording package recorded'])


def migration(evidence):
    journal = evidence['journal']
    if journal == 'damaged':
        return fact(None, unknown=['migration journal is damaged'])
    if journal and journal != 'indexed':
        return fact(journal, [f'migration journal phase {journal}'])
    if evidence['indexed']:
        return fact('indexed', ['source-layout.json entry'])
    if journal:
        return fact(None, unknown=['migration journal phase indexed, but source-layout.json has no entry'])
    if evidence['placed'] == 'archived':
        return fact(None, unknown=['archived without inventory or migration journal'])
    if evidence['placed'] is None:
        return fact(None, unknown=evidence['problems'])
    return fact('none', ['packet not migrated'])


def disagreements(record, evidence, registered):
    found = []
    if (record and evidence['location'] == 'archived' and evidence['journal'] in (None, 'indexed') and not declared(record)
            and not record.get('migration_authority') and not record.get('package')):
        found.append(LOW_LEVEL)
    if (record or {}).get('phase') == 'archived' and evidence['location'] in ('active', 'reserved', 'unused'):
        found.append('record says archived but the packet is not under runs/')
    if registered is False and evidence['session_material']:
        found.append('session material is present although no session registered; retirement as an unused preparation is refused')
    return found


def unused(row):
    """A preparation no session has registered with; only these are launched or retired."""
    return row['location'] in ('active', 'reserved') and row['lifecycle']['registered']['value'] is False


def open_engagement(row):
    """A registered session whose packet is still in the shared root, whatever its capture state."""
    return row['location'] == 'active' and row['lifecycle']['registered']['value'] is True


def outstanding(row):
    """Whether status lists a row: problems, unfinished work, and archived records without a package."""
    record = row['record'] or {}
    if row['problems']:
        return True
    if record.get('phase') == 'cancelled':
        return False
    if row['location'] == 'archived':
        return row['migration_pending'] or record.get('phase') == 'closing' or bool(row['record'] and not record.get('package'))
    return True


def authorized(row):
    """The custody step already authorized for a row: 'retire', 'preserve', 'resume-migration' or None."""
    record = row['record'] or {}
    if record.get('phase') == 'cancelling':
        return 'retire'
    if declared(record) and record.get('phase') != 'archived':
        return 'preserve'
    if row['migration_pending'] or (record.get('migration_authority') and record.get('phase') != 'archived'):
        return 'resume-migration'
    return None


def progress_state(record, migrated):
    if migrated:
        return 'archived' if record.get('package') else 'migrated; packaging pending'
    if declared(record):
        return 'ending recorded; preservation pending'
    return record.get('phase', 'unknown')


def describe(lifecycle, phase):
    """One status line; phase is the record's phase, or discovery's state without a record.

    A terminal callback or a capture closure is never called an engagement ending.
    """
    registered, capture = lifecycle['registered']['value'], lifecycle['capture']
    unknown = ['unknown: ' + ', '.join(lifecycle['unknown'])] if lifecycle['unknown'] else []
    if phase in CANCELLATION:
        return '; '.join(['unused preparation ' + ('being retired' if phase == 'cancelling' else 'retired'), *unknown])
    if capture['value'] == 'closed':
        closures = (f'{name} ({CLOSURE[name]}; not an ending)' for name in capture['closed_by'])
        parts = ['capture closed by ' + ' and '.join(closures)]
    elif registered is None:
        parts = ['starting state unknown']
    elif registered is False and lifecycle['migration']['value'] == 'none':
        start = 'launch attempted; no session registered yet' if phase == 'launching' else f'{phase}; not yet started'
        return '; '.join([start, *unknown])
    elif registered is False:
        parts = ['no capture session registered']
    elif capture['value'] == 'open':
        last = capture['last_event']
        parts = ['session registered; capture open ' + (f'after {MANUAL_EVENTS.get(last, last)}' if last else 'without a snapshot yet')]
    elif capture['value'] == 'displaced':
        parts = ['session displaced; awaiting its replacement registration']
    else:
        parts = ['session registered; capture state unknown']
    value = lifecycle['ending']['value']
    parts.append('ending declared' if value else 'no engagement ending declared' if value is False else 'ending unknown')
    stage, package = lifecycle['migration']['value'], lifecycle['packaged']['value']
    if stage == 'none':
        if value:
            parts.append('preservation pending')
    else:
        parts.append(f'migration {stage or "unknown"}')
        parts.append('recording package made' if package else 'recording package unknown' if package is None
                     else 'no recording package' if LOW_LEVEL in lifecycle['disagreements'] else 'recording package pending')
    return '; '.join(parts + unknown)
