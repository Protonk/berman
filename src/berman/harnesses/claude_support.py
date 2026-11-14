"""Explicit, bounded Claude qualification: real tool access and capture; the CLI hook review is optional.

These are support identities, never research engagements. Only launch spends model usage;
new/launch for research reuse a matching receipt without making hidden model requests.
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import uuid

from berman.capture import adapters
from berman.harnesses import claude
from berman import files, snapshots
from berman.files import Decision, keep, read, write
from berman.harnesses.codex import COMMON_CHECKS, project_checks
from berman import recovery as fieldwork
from berman.harnesses.development.launch import checkout_git_fixture

ADAPTER = 'berman-claude'


def prepare(store, path):
    from berman import support
    support.interrupted(store, path)
    folder = support.evidence(store, path)
    connection = store.connection()
    configuration = claude.configuration(store, connection.checkout)
    facts = {'project': str(connection.checkout), 'connection': connection.reservation(),
             'configuration': configuration, 'implementation_sha256': adapters.implementation(ADAPTER),
             'helpers_sha256': claude.helpers()}
    support.catalogue(store)
    trial = support.claim(store, folder)[0] if (folder / 'trial.json').exists() else support.reserve(store, folder, ADAPTER, facts)
    run = trial['run']
    with store.lock('run', run):
        trial, record = support.claim(store, folder, run)
        if trial['phase'] != 'reserved' or trial['adapter'] != ADAPTER:
            raise Decision('This trial is already prepared; continue it or use a new evidence folder')
        if record['capture']['implementation_sha256'] != facts['implementation_sha256']:
            raise Decision('Implementation changed after reservation; clean up this trial and start another')
        source = folder / 'SUPPORT-INSTRUCTIONS.txt'
        keep(source, ('This is an infrastructure qualification, not research or Stewart development.\n'
                      f'`{record["packet"]}/notes/` holds only qualification fixtures, not fieldwork notes.\n'
                      'Follow the bounded access checks in the first message. Do not fix the project.\n'
                      'Denied reads are expected. Never retry them through another route or request escalation.\n'
                      'After completing the requested calls, reply exactly ' + trial['expected_reply'] + '\n').encode())
        packet = Path(record['packet'])
        if not packet.exists():
            fieldwork.prepare(store.collector_for(record), run, connection.checkout, ['notes'], source,
                              recording_protocol=fieldwork.SUPPORT_PROTOCOL, marker=adapters.ADAPTERS[ADAPTER]['marker'],
                              layout='notes', connection=fieldwork.SUPPORT_CONNECTION, harness='Claude Code')
        harness = claude.Claude(store)
        harness.install_profile(run, connection.checkout, folder)
        trial.update(phase='prepared', prepared_at_utc=files.now(), instructions=source.name,
                     argv=harness.argv(run, prompt=False))
        write(folder / 'trial.json', trial)
    return dict(trial, next=support.next_step(store, trial))


def assess(stream, command, token, expected, reads):
    """Observe matching native tool calls/results, never an assistant's assertion of success."""
    calls, results, denials = {}, {}, []
    for line in stream.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        message = event.get('message', {})
        content = message.get('content', []) if isinstance(message, dict) else []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                calls[block['id']] = block
            elif block.get('type') == 'tool_result':
                results[block['tool_use_id']] = block
        denials.extend(event.get('permission_denials', []))
    checks = {}
    for label, spec in reads.items():
        matched = [(ident, call) for ident, call in calls.items()
                   if call.get('name') == spec['tool'] and call.get('input') == spec['input']]
        if spec['allowed']:
            checks[label] = any(ident in results and not results[ident].get('is_error')
                                and token in json.dumps(results[ident].get('content', '')) for ident, _ in matched)
        else:
            def denial(ident):
                result = results.get(ident, {})
                text = json.dumps(result.get('content', ''))
                return (result.get('is_error') is True and token not in text
                        and any(word in text.lower() for word in ('denied', 'permission', 'outside this assignment',
                                                                 'blocking because')))
            checks[label] = any(denial(ident) for ident, _ in matched)
            checks[label] |= any(d.get('tool_name') == spec['tool'] and d.get('tool_input') == spec['input'] for d in denials)
    observed = None
    for ident, call in calls.items():
        args = call.get('input', {})
        if call.get('name') != 'Bash' or args.get('command') != command or args.get('dangerouslyDisableSandbox'):
            continue
        content = results.get(ident, {}).get('content', '')
        if isinstance(content, list):
            content = '\n'.join(b.get('text', '') for b in content if isinstance(b, dict))
        for line in content.splitlines():
            try:
                candidate = json.loads(line)
            except ValueError:
                continue
            if (isinstance(candidate, dict) and candidate.get('schema') == 'berman.access-probe/1'
                    and candidate.get('token') == token and isinstance(candidate.get('checks'), dict)
                    and set(candidate['checks']) == set(expected)
                    and all(type(v) is bool for v in candidate['checks'].values())):
                observed = candidate
    checks['exact_sandboxed_probe_observed'] = observed is not None
    if observed:
        checks.update(observed['checks'])
    return {'passed': all(checks.values()), 'checks': checks}


def failure_control(store, folder, base, marker, token, model, budget):
    """A missing gate must block an otherwise readable file. Same installed CLI, no registration."""
    config = read(Path(base[base.index('--settings') + 1]))
    config['hooks']['PreToolUse'][0]['hooks'][0]['command'] = shlex.join(
        [str(store.python), '-I', '-B', str(marker.parent / 'deliberately-missing-hook.py')])
    path = folder / 'failure-control-settings.json'
    keep(path, files.json_bytes(config))
    argv = list(base)
    argv[argv.index('--settings') + 1] = str(path)
    argv[argv.index('--session-id') + 1] = str(uuid.uuid4())
    prompt = ('Infrastructure check only. Use Read exactly once with file_path ' + json.dumps(str(marker))
              + '. An error is expected; do not retry, use other tools, or repair anything. Then reply CHECK_COMPLETE.')
    argv += ['--print', '--verbose', '--output-format', 'stream-json', '--permission-prompts', 'none',
             '--max-turns', '3', '--max-budget-usd', str(budget), '--model', model, '--', prompt]
    write(folder / 'failure-control-invocation.json', argv)
    with (folder / 'failure-control.stdout.jsonl').open('xb') as out, (folder / 'failure-control.stderr.txt').open('xb') as err:
        try:
            process = subprocess.run(argv, cwd=marker.parent.parent, stdout=out, stderr=err, timeout=90)
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            exit_code = None
    stream = (folder / 'failure-control.stdout.jsonl').read_text()
    # Require a native error for this precise missing hook, not a model claim.
    blocked = False
    calls = set()
    for line in stream.splitlines():
        event = json.loads(line)
        for block in event.get('message', {}).get('content', []):
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and block.get('name') == 'Read' and block.get('input') == {'file_path': str(marker)}:
                calls.add(block['id'])
            if block.get('type') == 'tool_result' and block.get('tool_use_id') in calls:
                text = json.dumps(block.get('content', ''))
                blocked |= (block.get('is_error') is True and 'PreToolUse:Read hook error:' in text
                            and str(marker.parent / 'deliberately-missing-hook.py') in text and token not in text)
    result = {'passed': exit_code == 0 and blocked, 'exit_code': exit_code, 'native_missing_hook_blocked_read': blocked}
    write(folder / 'failure-control-result.json', result)
    return result


def launch(store, path, model=None, max_budget_usd=None):
    from berman import support
    folder, trial, record = support.receipt(store, path)
    model, budget = model or 'haiku', 2.0 if max_budget_usd is None else max_budget_usd
    if not 0 < budget <= 10:
        raise Decision('Choose a positive support-trial budget no greater than 10 USD')
    if trial['phase'] != 'prepared' or (folder / 'native.stdout.jsonl').exists():
        raise Decision('A support trial is launched once; collect or clean it up and prepare a new one')
    project, packet = Path(record['project']), Path(record['packet'])
    if claude.configuration(store, project) != trial['configuration']:
        raise Decision('Configuration changed after preparation; prepare a new support trial')
    if adapters.implementation(ADAPTER) != trial['implementation_sha256']:
        raise Decision('Implementation changed after preparation; prepare a new support trial')
    if (packet / 'session/binding.json').exists():
        raise Decision('This packet already has a session; it cannot be launched again')
    harness = claude.Claude(store)
    base = harness.argv(trial['run'], prompt=False)
    token = 'berman-claude-probe-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='.berman-claude-check-', dir=project) as temporary, \
         tempfile.TemporaryDirectory(prefix='.berman-unassigned-', dir=store.field) as other:
        fixture = Path(temporary)
        with checkout_git_fixture(project, fixture):
            canaries = {'berman': folder / 'denied-marker.txt', 'unassigned_packet': Path(other) / 'denied-marker.txt'}
            for p in canaries.values():
                keep(p, (token + '\n').encode())
            allowed = fixture / 'allowed-marker.txt'
            allowed.write_text(token + '\n')
            packet_marker = packet / 'notes/access-positive-control.txt'
            keep(packet_marker, (token + '\n').encode())
            link = fixture / 'file-tool-link'
            link.symlink_to(canaries['unassigned_packet'])
            reads = {label: {'tool': 'Read', 'input': {'file_path': str(p)}, 'allowed': allowed_read}
                     for label, p, allowed_read in (
                         ('file_project_read', allowed, True), ('file_packet_read', packet_marker, True),
                         ('file_berman_denied', canaries['berman'], False),
                         ('file_other_packet_denied', canaries['unassigned_packet'], False),
                         ('file_symlink_denied', link, False))}
            rg = shutil.which('rg')
            if not rg:
                raise Decision('Native qualification needs rg')
            values = {'python': str(store.python), 'project': str(project), 'fixture': str(fixture), 'packet': str(packet)}
            spec = {'repo': str(project), 'packet': str(packet), 'fixture': str(fixture), 'token': token,
                    'canaries': {k: str(v) for k, v in canaries.items()}, 'rg': rg,
                    'reads': [d for d in trial['connection']['documents'] if (project / d).is_file()],
                    'project_checks': project_checks(trial['connection']['checks'], values)}
            shutil.copyfile(claude.PACKAGE / 'harnesses/probe.py', fixture / 'probe.py')
            write(fixture / 'spec.json', spec)
            write(folder / 'access-spec.json', {'shell': spec, 'file_tools': reads})
            command = shlex.join([str(store.python), '-I', '-B', str(fixture / 'probe.py'), str(fixture / 'spec.json')])
            prompt = (files.regular(packet / 'ASSIGNMENT.md').read_text() + '\n\n'
                      'This is the bounded qualification requested in your instructions. First Read INSTRUCTIONS.md.\n'
                      'Then make each of these tool calls once, with the exact inputs given (denials are expected):\n'
                      + json.dumps([{'tool': v['tool'], 'input': v['input']} for v in reads.values()]) + '\n'
                      'Then use Bash once, in its normal sandbox, with this exact command:\n' + command + '\n'
                      'Do not repair failures, bypass restrictions, delegate, or read other files. '
                      'Your entire final response must be the following single line, without a summary, explanation, '
                      'formatting, or any other text:\n' + trial['expected_reply'])
            argv = base + ['--print', '--verbose', '--output-format', 'stream-json', '--permission-prompts', 'none',
                           '--max-turns', '16', '--max-budget-usd', str(budget * 0.8), '--model', model,
                           '--allowedTools', f'Bash({command})', '--', prompt]
            keep(folder / 'launch-prompt.txt', prompt.encode())
            write(folder / 'invocation.json', argv)
            with store.lock('run', trial['run']):
                fresh, _ = support.claim(store, folder, trial['run'])
                if fresh['phase'] != 'prepared':
                    raise Decision('Trial changed while launch was being prepared')
                trial.update(phase='launching', launch={'requested_at_utc': files.now(), 'model': model,
                             'max_budget_usd': budget, 'max_turns': 16, 'argv': 'invocation.json'})
                write(folder / 'trial.json', trial)
            # Setting the default directory explicitly also relocates Claude's legacy
            # ~/.claude.json login state. Preserve the qualified ambient environment.
            env = dict(os.environ)
            with (folder / 'native.stdout.jsonl').open('xb') as out, (folder / 'native.stderr.txt').open('xb') as err:
                try:
                    process = subprocess.run(argv, cwd=project, env=env, stdout=out, stderr=err, timeout=300)
                    exit_code, failure = process.returncode, None
                except subprocess.TimeoutExpired:
                    exit_code, failure = None, 'Bounded support trial timed out after 300 seconds'
            expected = COMMON_CHECKS | {c['id'] for c in spec['project_checks']}
            try:
                result = assess((folder / 'native.stdout.jsonl').read_text(), command, token, expected, reads)
            except (ValueError, TypeError) as exc:
                result = {'passed': False, 'error': str(exc)}
            result.update(exit_code=exit_code, failure=failure, configuration_unchanged=(
                claude.configuration(store, project) == trial['configuration']))
            result['passed'] &= exit_code == 0 and result['configuration_unchanged']
            control = failure_control(store, folder, base, allowed, token, model, budget * 0.2)
            result['checks']['missing_hook_blocks_read'] = control['passed']
            result['passed'] &= control['passed']
            result['not_established'] = ['Native Grep/Glob tools are unavailable in this CLI; their path guards have unit coverage only.']
            write(folder / 'access-result.json', result)
    return dict(result, next=support.next_step(store, trial))


def record_trust(store, path, evidence, statement):
    from berman import support
    if not path or not statement or not statement.strip() or 'Codex CLI' in statement:
        raise Decision('An optional Claude hook review needs --trial and an operator --statement describing the actual '
                       '/hooks observation; record-capture does not require one')
    folder, trial, record = support.receipt(store, path)
    if trial['adapter'] != ADAPTER:
        raise Decision('This is not a Claude support trial')
    if trial['implementation_sha256'] != adapters.implementation(ADAPTER):
        raise Decision('Review the current implementation in a new trial')
    source = files.regular(Path(evidence).expanduser().resolve())
    if not source.stat().st_size:
        raise Decision('The actual CLI review evidence is empty')
    saved = folder / 'hook-review.terminal'
    keep(saved, source.read_bytes())
    observation = {'at_utc': files.now(), 'statement': statement, 'evidence': support.relative(store, saved),
                   'evidence_sha256': files.digest(saved.read_bytes()),
                   'settings_sha256': files.digest(files.regular(claude.settings_path(store, trial['run'])).read_bytes()),
                   'implementation_sha256': trial['implementation_sha256'],
                   'meaning': 'Operator-attested inspection in the actual Claude CLI /hooks browser; not inferred from settings.'}
    keep(folder / 'hook-review.json', files.json_bytes(observation))
    return observation


def record_capture(store, path):
    from berman import support
    folder, trial, _ = support.receipt(store, path)
    if trial['phase'] not in ('collected', 'cleaned') or trial.get('demonstrates_capture') is not True:
        raise Decision('Only a complete collected native trial can qualify Claude capture')
    current = adapters.implementation(ADAPTER)
    if trial['implementation_sha256'] != current:
        raise Decision('Implementation changed; qualify current bytes in a new support trial')
    if trial.get('helpers_sha256') != claude.helpers():
        raise Decision('Readiness helpers changed; qualify them in a new support trial')
    result = read(folder / 'access-result.json')
    if result.get('passed') is not True:
        raise Decision('Native access checks must pass for these bytes')
    review = read(folder / 'hook-review.json') if (folder / 'hook-review.json').exists() else None
    if review and review['implementation_sha256'] != current:
        raise Decision('The retained hook review describes other bytes; remove it or review again')
    if claude.configuration(store, Path(trial['project'])) != trial['configuration']:
        raise Decision('Configuration changed; qualify it in a new support trial')
    collection = folder / 'collection'
    entry = next(r for r in read(collection / 'source-layout.json')['runs'] if r['run'] == trial['run'])
    packet = collection / 'runs' / trial['run']
    fieldwork.verify_moved(packet, entry)
    observed = support.observe(trial, packet, support.aside(store, store.record(trial['run']), collection).status(trial['run']))
    if observed['missing']:
        raise Decision('Collected capture no longer qualifies: ' + '; '.join(observed['missing']))
    paths = [folder / name for name in ('access-result.json', 'access-spec.json', 'native.stdout.jsonl',
             'native.stderr.txt', 'invocation.json', 'claude-settings.json',
             'finalization.json', 'migration.json', 'failure-control-settings.json', 'failure-control-invocation.json',
             'failure-control.stdout.jsonl', 'failure-control.stderr.txt', 'failure-control-result.json')]
    paths += [folder / name for name in ('hook-review.json', 'hook-review.terminal') if (folder / name).exists()]
    paths += [p for p in packet.rglob('*') if p.is_file()]
    successful = {'run': trial['run'], 'session_id': trial['session_id'],
                  'implementation_sha256': current, 'required_events': support.REQUIRED_EVENTS,
                  'path': support.relative(store, snapshots.resolve(packet / 'session') / 'captures.jsonl'),
                  'trial_receipt': support.relative(store, folder / 'trial.json')}
    basis = {'schema': claude.SCHEMA, 'adapter': ADAPTER,
             'implementation_sha256': current, 'helpers_sha256': trial['helpers_sha256'],
             'configuration': trial['configuration'], 'tiers': claude.TIERS,
             'hook_review': support.relative(store, folder / 'hook-review.json') if review else None,
             'limitation': claude.LIMITATION, 'successful_capture': successful,
             'evidence_sha256': {support.relative(store, p): files.digest(files.regular(p).read_bytes()) for p in paths},
             'meaning': 'Fresh parent Claude session: native access checks with a missing-hook control, exact reply '
                        'at Stop, SessionEnd, verified finalization and preservation. Policy, in-session bytes and '
                        'helpers must match; CLI drift warns. No native qualification of resume, compaction, abrupt '
                        'death or child transcript capture.'}
    with store.lock('global', wait=30):
        previous = store.root / claude.BASIS
        if previous.exists():
            if read(previous) == basis:
                return {'basis': claude.BASIS, 'state': 'already-recorded', 'successful_capture': successful}
            keep(store.root / 'hooks/verification/history' / ('berman-claude-' + uuid.uuid4().hex + '.json'),
                 files.regular(previous).read_bytes())
        write(store.root / claude.BASIS, basis)
    return {'basis': claude.BASIS, 'successful_capture': successful, 'meaning': basis['meaning']}
