"""Sequences of operator commands, session callbacks, lock contention and interruptions.

A state machine drives the real workflow in a disposable workspace. Every command may be
interrupted at any of its durable writes, before or after the write lands, and is then retried
as an operator would retry it. Whatever the order:

- a retry completes the interrupted command and reaches the state it was asked for (one exception:
  once a cancelled packet has been moved aside, the next preparation finishes its retirement, and
  repeating cancel is refused without change);
- a retry allocates no second identity and never re-declares an ending;
- only a command that carries the operator's declaration ends an engagement: callbacks,
  finalization, preparation of another run, cancellation and contention never do;
- an archived run's packet and package never change again;
- a command refused for a held lock changes nothing;
- after every step, status works, reports every unfinished run and leaves no lock held.

An interruption is modelled as an OSError raised from berman.files.atomic. That is the write
every durable record goes through, and the frozen collector's own writes are not interrupted.
A process killed outright would also skip cleanup that an exception still runs; that case is
not modelled.
"""

from pathlib import Path
import tempfile
from unittest import mock

from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from berman import files
from berman.files import Decision, digest_json, read, write
from berman.lifecycle import declared
from berman.records import Store
from berman.workflow import Workflow
from tests.support.properties import budget
from tests.support.sessions import callback, start
from tests.support.workspace import FixtureHarness, git_repository, workspace

STEPS = ('work-accounts', 'participant-inquiry', 'work-accounts', 'participant-inquiry')
ORDER = ('reserved', 'prepared', 'ready', 'launching', 'active', 'closing', 'archived')
RETIREMENT = ('cancelling', 'cancelled')
CONNECTION = 'stewart'


class Interruption:
    """Stand in for files.atomic and fail on one chosen call, before or after that write lands."""

    def __init__(self, at, after):
        self.at, self.after, self.calls, self.fired = at, after, 0, False
        self.real = files.atomic

    def __call__(self, path, data):
        self.calls += 1
        if self.calls == self.at:
            self.fired = True
            if self.after:
                self.real(path, data)
            raise OSError('injected interruption')
        return self.real(path, data)


class WorkflowSequences(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name).resolve()
        self.root, project = base / 'berman', base / 'stewart'
        (base / 'codex').mkdir()
        workspace(self.root, base, project, documents=['README.md'])
        git_repository(project, {'README.md': 'Project context\n'})
        plan = read(self.root / 'plans/stewart-fieldwork-roles/plan.json')
        plan['steps'] = [{'id': f'step-{n}', 'recipe': recipe} for n, recipe in enumerate(STEPS, 1)]
        write(self.root / 'plans/stewart-fieldwork-roles/plan.json', plan)
        self.store = Store(self.root)
        self.workflow = Workflow(self.store, FixtureHarness(self.store))
        self.seen = {}  # run -> what was last observed of it
        self.issued = []  # every identity ever seen, in order of appearance
        self.unfinished = None  # (command, arguments, identities before its first attempt) awaiting a retry
        self.reasons = 0

    def teardown(self):
        self.temporary.cleanup()

    # ---- observation

    def observe(self):
        seen = {}
        for row in self.store.discover():
            record = row['record'] or {}
            archived = self.root / 'runs' / row['run']
            package = record.get('package') or {}
            seen[row['run']] = {
                'phase': record.get('phase'),
                'location': row['location'],
                'registered': row['lifecycle']['registered']['value'],
                'declared': (record['ending']['declared_at_utc'], record['ending']['reason'])
                if declared(record)
                else None,
                'frozen': 'finalized.json' in row['lifecycle']['capture']['closed_by'],
                'step': record.get('step'),
                'problems': list(row['problems']),
                'package': (package.get('sha256'), files.digest(Path(package['path']).read_bytes()))
                if package
                else None,
                'packet': digest_json(files.inventory(archived)) if record.get('phase') == 'archived' else None,
            }
        return seen

    def tree(self, beyond_session_notes=False):
        """Every file the workflow owns, with its bytes; lock files are bookkeeping, not records.

        Any command first notes in a run's record that a session has registered with it, before it
        decides anything. With beyond_session_notes, that note is left out of the comparison.
        """
        found = {}
        for base in (self.root / 'operations', self.root / 'runs', self.root / 'archive', self.store.field):
            for path in sorted(base.rglob('*')) if base.exists() else ():
                if path.is_file() and not path.is_symlink():
                    found[str(path)] = files.digest(path.read_bytes())
                    if beyond_session_notes and path.name == 'operation.json':
                        noted = {k: v for k, v in read(path).items() if k not in ('started_session', 'phase')}
                        found[str(path)] = digest_json(noted)
        for name in ('source-layout.json', 'migrations'):
            path = self.root / name
            for item in [path] if path.is_file() else sorted(path.rglob('*.json')) if path.exists() else []:
                found[str(item)] = files.digest(item.read_bytes())
        return found

    def advance(self, may_declare=()):
        """Compare what is now recorded with what was, allowing endings only for the named runs."""
        now = self.observe()
        fresh = [run for run in now if run not in self.seen]
        assert len(fresh) <= 1, f'one step allocated several identities: {fresh}'
        assert set(self.seen) <= set(now), f'an identity disappeared: {set(self.seen) - set(now)}'
        for run in fresh:
            assert all(int(run[4:]) > int(old[4:]) for old in self.issued), f'{run} is not later than {self.issued}'
            self.issued.append(run)
        for run, current in now.items():
            assert not current['problems'], f'{run}: {current["problems"]}'
            before = self.seen.get(run)
            if current['declared'] and not (before and before['declared']):
                assert run in may_declare, f'{run} acquired an ending no operator declared in this step'
            if current['location'] == 'archived':
                assert current['declared'], f'{run} left the packet root without a declared ending'
            if not before:
                continue
            if before['declared']:
                assert current['declared'] == before['declared'], f'{run}: its declaration changed: {before} {current}'
            if before['packet']:
                assert current['packet'] == before['packet'], f'{run}: its archived packet changed'
                assert current['package'] == before['package'], f'{run}: its recording package changed'
            if before['registered']:
                assert current['registered'], f'{run}: a registered session was forgotten'
            assert self.forward(before['phase'], current['phase']), (
                f'{run}: {before["phase"]} became {current["phase"]}'
            )
        self.seen = now

    @staticmethod
    def forward(before, after):
        if before == after or (before, after) == ('ready', 'prepared'):
            return True  # Preparing a waiting packet again re-establishes its readiness.
        if before in RETIREMENT:
            return (before, after) == ('cancelling', 'cancelled')
        if after in RETIREMENT:
            return ORDER.index(before) <= ORDER.index('ready')
        return ORDER.index(before) < ORDER.index(after)

    # ---- the model's reading of what is recorded

    def runs(self, kind):
        chosen = []
        for run, state in sorted(self.seen.items()):
            unstarted = state['location'] in ('active', 'reserved') and state['registered'] is False
            match kind:
                case 'prepared':
                    found = unstarted and state['phase'] == 'ready'
                case 'unstarted':
                    found = unstarted and state['phase'] not in RETIREMENT
                case 'open':
                    found = state['location'] == 'active' and state['registered'] is True
                case 'undeclared':
                    found = state['location'] == 'active' and state['registered'] is True and not state['declared']
                case 'archived':
                    found = state['phase'] == 'archived'
            if found:
                chosen.append(run)
        return chosen

    def started(self):
        return [run for run, state in self.seen.items() if state['registered']]

    def reason(self, what):
        self.reasons += 1
        return f'{what} {self.reasons}: the researcher said so'

    # ---- commands, each a (name, arguments) pair so that a retry is the same command

    def execute(self, command, arguments):
        if command == 'prepare':
            return self.workflow.new(keep_open=arguments['keep_open'])
        if command == 'combined':
            return self.workflow.new(finish_previous=arguments['reason'])
        if command == 'finish':
            return self.workflow.finish(arguments['reason'], arguments['run'])
        return self.workflow.cancel(arguments['reason'], arguments['run'])

    def expected(self, command, arguments):
        """Whether the command should complete from the recorded situation, and which run it may end."""
        exhausted = len(self.started()) >= len(STEPS)
        if command == 'prepare':
            return not exhausted and (arguments['keep_open'] or not self.runs('undeclared')), None
        if command == 'combined':
            single = self.runs('open') if len(self.runs('open')) == 1 else []
            return bool(single) and not exhausted, single[0] if single else None
        if command == 'finish':
            # Finishing an archived run again is answered without change, whatever else is open.
            return arguments['run'] in self.runs('open') + self.runs('archived'), arguments['run']
        return arguments['run'] in self.runs('unstarted'), None

    def completed(self, command, arguments, result, before):
        """The state a completed command was asked for, whether it completed at once or on a retry."""
        new = [run for run in self.seen if run not in before]
        if command in ('prepare', 'combined'):
            assert result['state'] == 'ready', result
            waiting = [
                run
                for run, state in before.items()
                if state['location'] in ('active', 'reserved')
                and state['registered'] is False
                and state['phase'] not in RETIREMENT
            ]
            assert new == ([] if waiting else [result['run']]), f'{command} retried into another identity: {new}'
            assert result['run'] in (waiting or new), result
            assert self.seen[result['run']]['phase'] == 'ready'
        if command == 'combined':
            previous = arguments['previous']
            assert self.seen[previous]['phase'] == 'archived', self.seen[previous]
            assert self.seen[previous]['declared'][1] == arguments['reason']
        if command == 'finish':
            assert result['state'] in ('archived', 'already-archived'), result
            state = self.seen[arguments['run']]
            assert state['phase'] == 'archived', state
            if before[arguments['run']]['declared']:
                # A second declaration never replaces the first, whatever reason it gives.
                assert state['declared'] == before[arguments['run']]['declared'], state
            else:
                assert state['declared'][1] == arguments['reason'], state
            assert new == []
        if command == 'cancel':
            assert self.seen[arguments['run']]['phase'] == 'cancelled'
            assert new == []

    def attempt(self, command, arguments, interruption=None):
        succeeds, ends = self.expected(command, arguments)
        if command == 'combined':
            arguments = dict(arguments, previous=ends)
        before, files_before = dict(self.seen), self.tree(beyond_session_notes=True)
        try:
            if interruption:
                with mock.patch.object(files, 'atomic', interruption):
                    result = self.execute(command, arguments)
            else:
                result = self.execute(command, arguments)
        except Decision as refusal:
            assert not succeeds, f'{command} {arguments} was refused: {refusal}'
            changed = self.tree(beyond_session_notes=True)
            assert changed == files_before, f'a refused {command} changed files: {refusal}'
            self.advance()
            return
        except OSError as failure:
            assert interruption and interruption.fired, failure
            self.advance(may_declare=[ends] if ends else ())
            # A command that was going to be refused wrote nothing before it was interrupted, if it wrote at all.
            if succeeds:
                self.unfinished = (command, arguments, before)
            return
        assert succeeds, f'{command} {arguments} completed where a refusal was expected: {result}'
        self.advance(may_declare=[ends] if ends else ())
        self.completed(command, arguments, result, before)

    # ---- rules: operator commands

    interruptions = st.builds(Interruption, st.integers(1, 24), st.booleans())

    @precondition(lambda self: not self.unfinished)
    @rule(keep_open=st.booleans(), interruption=st.none() | interruptions)
    def prepare(self, keep_open, interruption):
        self.attempt('prepare', {'keep_open': keep_open}, interruption)

    @precondition(lambda self: not self.unfinished and self.seen)
    @rule(data=st.data(), interruption=st.none() | interruptions)
    def finish(self, data, interruption):
        run = data.draw(st.sampled_from(sorted(self.seen)), label='run')
        self.attempt('finish', {'run': run, 'reason': self.reason('finish')}, interruption)

    @precondition(lambda self: not self.unfinished and self.runs('open'))
    @rule(interruption=st.none() | interruptions)
    def finish_previous_and_prepare(self, interruption):
        self.attempt('combined', {'reason': self.reason('combined')}, interruption)

    @precondition(lambda self: not self.unfinished and self.seen)
    @rule(data=st.data(), interruption=st.none() | interruptions)
    def cancel(self, data, interruption):
        run = data.draw(st.sampled_from(sorted(self.seen)), label='run')
        self.attempt('cancel', {'run': run, 'reason': self.reason('cancel')}, interruption)

    @precondition(lambda self: self.unfinished)
    @rule(interruption=st.none() | interruptions)
    def retry(self, interruption):
        """The same command again, as the operator is told to; it may be interrupted again."""
        command, arguments, before = self.unfinished
        ends = arguments.get('previous') or arguments.get('run') if command in ('combined', 'finish') else None
        if command == 'cancel' and self.seen[arguments['run']]['location'] == 'unused':
            # Once the packet has been moved aside, repeating cancel does not finish the retirement.
            # It is refused and changes nothing; status lists the run until the next preparation
            # settles it. A limitation, kept. Before the move, repeating cancel resumes it.
            files_before = self.tree()
            try:
                self.execute(command, arguments)
            except Decision as refusal:
                assert 'Expected one prepared packet' in str(refusal), refusal
            else:
                raise AssertionError('a packet already moved aside was cancelled a second time')
            assert self.tree() == files_before
            self.advance()
            self.unfinished = None
            return
        try:
            if interruption:
                with mock.patch.object(files, 'atomic', interruption):
                    result = self.execute(command, arguments)
            else:
                result = self.execute(command, arguments)
        except OSError as failure:
            assert interruption and interruption.fired, failure
            self.advance(may_declare=[ends] if ends else ())
            return
        self.advance(may_declare=[ends] if ends else ())
        self.completed(command, arguments, result, before)
        self.unfinished = None

    # ---- rules: what happens without the operator

    @precondition(lambda self: not self.unfinished and self.runs('prepared'))
    @rule(data=st.data())
    def session_registers(self, data):
        run = data.draw(st.sampled_from(self.runs('prepared')), label='run')
        start(self.store, run)
        self.advance()
        assert self.seen[run]['registered'] is True

    @precondition(lambda self: self.runs('open'))
    @rule(data=st.data(), event=st.sampled_from(('Stop', 'SessionEnd')))
    def callback_arrives(self, data, event):
        run = data.draw(st.sampled_from(self.runs('open')), label='run')
        callback(self.store, run, event)
        self.advance()  # A terminal callback closes nothing and ends nothing.

    @precondition(lambda self: [run for run in self.runs('open') if not self.seen[run]['frozen']])
    @rule(data=st.data())
    def capture_is_finalized(self, data):
        run = data.draw(st.sampled_from([r for r in self.runs('open') if not self.seen[r]['frozen']]), label='run')
        self.store.collector_for(self.store.record(run)).command('finalize', run)
        self.advance()  # Frozen bytes, and still no ending: nothing may be declared here.
        state = self.seen[run]
        assert (
            state['frozen']
            and state['location'] == 'active'
            and not (state['declared'] and state['phase'] == 'archived')
        )
        status = {row['run']: row for row in self.workflow.status()['runs']}[run]
        assert 'not an ending' in status['summary'], status['summary']

    @precondition(lambda self: self.seen)
    @rule(command=st.sampled_from(('prepare', 'finish', 'cancel')), data=st.data())
    def another_operation_holds_the_connection(self, command, data):
        run = data.draw(st.sampled_from(sorted(self.seen)), label='run')
        arguments = {'keep_open': True, 'run': run, 'reason': 'refused while locked'}
        before = self.tree()
        with self.store.lock('connection', CONNECTION):
            try:
                self.execute(command, arguments)
            except Decision as refusal:
                assert 'Another Berman operation is using connection stewart' in str(refusal), refusal
            else:
                raise AssertionError(f'{command} ran while another operation held the connection')
        assert self.tree() == before, f'a locked-out {command} changed files'
        self.advance()

    # ---- after every step

    @invariant()
    def status_reports_everything_unfinished_and_holds_no_lock(self):
        before = self.tree()
        status = self.workflow.status()
        self.workflow.cheatsheet()
        assert self.tree() == before, 'status or cheatsheet changed files'
        assert status['operations_in_progress'] == [] and self.store.occupied() == []
        assert status['workspace_problems'] == []
        listed = {row['run']: row for row in status['runs']}
        for run, state in self.seen.items():
            if state['phase'] in ('archived', 'cancelled'):
                assert run not in listed, f'{run} is finished but still listed'
                continue
            assert run in listed, f'{run} is unfinished ({state["phase"]}) and status does not list it'
            assert listed[run]['ending_declared'] == bool(state['declared'])

    @invariant()
    def steps_are_started_once_and_in_order(self):
        taken = sorted((int(run[4:]), state['step']) for run, state in self.seen.items() if state['registered'])
        assert [step for _, step in taken] == [f'step-{n}' for n in range(1, len(taken) + 1)], taken

    @invariant()
    def a_prepared_packet_keeps_the_assignment_it_was_given(self):
        for run in self.runs('prepared'):
            for name in ('ASSIGNMENT.md', 'INSTRUCTIONS.md', 'run.json'):
                kept = self.store.folder(run) / 'preparation' / name
                assert (self.store.field / run / name).read_bytes() == kept.read_bytes(), f'{run}/{name} changed'


WorkflowSequences.TestCase.settings = budget(examples=20, steps=20)
TestWorkflowSequences = WorkflowSequences.TestCase


def load_tests(loader, standard, pattern):
    # Hypothesis exposes the machine as a TestCase; the rule class itself is not a test.
    return loader.loadTestsFromTestCase(TestWorkflowSequences)
