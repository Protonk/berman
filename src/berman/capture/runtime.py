"""Routed collectors with exclusive session ownership and independent packet locks.

The frozen collector they build on remains unchanged. Only the router installs the Codex
collector; no direct collector handler is installed alongside it.
"""
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import fcntl
import os
from pathlib import Path
import re
import time

from berman.capture import collector

capture = collector.capture
_current_run = ContextVar('berman_capture_run', default=None)


def marker_warning(prompt, marker=None):
    """Warn about near markers without registering or opening a packet."""
    if not isinstance(prompt, str):
        return {}
    expected = collector.MARKER
    if marker is not None and marker != expected:
        return {}
    if re.search(r'(?im)^\s*(?:[*_>`~]+\s*)?Berman-Fieldwork-Run\s*:', prompt):
        return {'systemMessage': 'Berman fieldwork capture: no new registration. Use exactly '
                + expected + 'run-NNN as the complete first nonblank line, without formatting.'}
    # The opening line of the removed legacy-codex adapter registers nothing; say so rather than stay silent.
    if re.search(r'(?im)^\s*(?:[*_>`~]+\s*)?Stewart-Fieldwork-Run\s*:', prompt):
        return {'systemMessage': 'Berman fieldwork capture: no registration. The Stewart-Fieldwork-Run opening line '
                'belonged to a removed adapter; fieldwork packets now open with ' + expected + 'run-NNN.'}
    return {}


@contextmanager
def file_lock(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f'Symlink in capture lock path: {path}')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + 0.6
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError(f'Another capture is still running for {path.stem}; retry after it finishes')
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)


class Routed:
    adapter = None
    marker = None
    bound_run = None

    def for_run(self, run):
        result = type(self)(self.fieldwork, self.codex_home, self.analysis)
        result.bound_run = run
        return result

    @contextmanager
    def run_scope(self, run):
        token = _current_run.set((str(self.analysis), run))
        try:
            yield
        finally:
            _current_run.reset(token)

    def locked(self):
        if any(p.is_symlink() for p in (self.state, *self.state.parents)):
            raise ValueError('Symlink in collector state directory')
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        scope = _current_run.get()
        run = scope[1] if scope and scope[0] == str(self.analysis) else self.bound_run
        if run is not None and not capture.RUN.fullmatch(run):
            raise ValueError('Invalid capture run identity')
        return file_lock(self.analysis / '.berman-locks' / ('capture-' + (run or 'preparation') + '.lock'))

    def session_lock(self, sid):
        self.registry(sid)  # Validate before using the identity in a path.
        return file_lock(self.codex_home / 'berman-session-locks' / (sid + '.lock'))

    def check_owner(self, sid):
        self.registry(sid)  # Validate the identity; each adapter has its own registry namespace.

    def register(self, event, run):
        self.check_owner(event['session_id'])
        result = super().register(event, run)
        path = self.registry(event['session_id'])
        state = capture.read_json(path)
        state.update(workspace=str(self.analysis), harness='codex', adapter=self.adapter,
                     packet=str(self.fieldwork / run))
        capture.atomic(path, capture.json_bytes(state))
        return result

    def hook(self, event):
        if event.get('hook_event_name') not in ('UserPromptSubmit', 'Stop', 'Interrupt', 'SessionEnd'):
            return {}
        sid = event.get('session_id')
        path = self.registry(sid)
        prompt = event.get('prompt', '')
        first = next((s for s in prompt.splitlines() if s.strip()), '') if isinstance(prompt, str) else ''
        opening = event.get('hook_event_name') == 'UserPromptSubmit' and first.startswith(self.marker)
        # Irrelevant hooks must remain silent, including the other adapter's opening.
        if not opening and not path.exists():
            return marker_warning(prompt, self.marker) if event.get('hook_event_name') == 'UserPromptSubmit' else {}
        with self.session_lock(sid):
            state = capture.read_json(path) if path.exists() else {}
            if state.get('workspace') not in (None, str(self.analysis)):
                return {}
            self.check_owner(sid)
            run = first[len(self.marker):] if opening else state.get('run')
            with self.run_scope(run):
                return super().hook(event)

    def command(self, name, run, **kwargs):
        if name == 'status':
            return super().command(name, run, **kwargs)
        root = self.run_path(run)
        binding = root / 'session/binding.json'
        sid = capture.read_json(binding)['session_id'] if binding.exists() else kwargs.get('session_id')
        with self.session_lock(sid) if sid else nullcontext():
            if sid:
                self.check_owner(sid)
            with self.run_scope(run):
                return super().command(name, run, **kwargs)


class Collector(Routed, collector.Collector):
    adapter = 'berman-codex'
    marker = collector.MARKER

    def at(self, packet_root):
        return self if Path(packet_root) == self.fieldwork else type(self)(packet_root, self.codex_home, self.analysis)
