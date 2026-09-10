"""Cleanup authority for resources created by one explicit live demo only."""
from contextlib import AbstractContextManager
import signal
import subprocess
import sys
import threading
import time


class DemoLifetime(AbstractContextManager):
    def __init__(self, *, cleanup=True):
        self.cleanup = cleanup
        self.workers, self.sessions, self.handlers = [], [], {}

    def __enter__(self):
        if self.cleanup and threading.current_thread() is threading.main_thread():
            for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                self.handlers[number] = signal.getsignal(number)
                signal.signal(number, self._interrupt)
        return self

    @staticmethod
    def _interrupt(number, frame):
        raise SystemExit(128 + number)

    def own_session(self, manager, session_id, name):
        if self.cleanup:
            removal = manager.prepare_removal({'session_id': session_id, 'session': name})
            self.sessions.append((manager, removal))

    def close(self):
        import psutil
        from .process_actions import ProcessActionError, _identity, _process, _tree
        from .sessions import SessionError

        warnings, targets = [], {}
        for worker in self.workers:
            # An unreaped Popen child cannot have its PID recycled. Descendants
            # are still captured and identity-checked before sending signals.
            if worker.poll() is None:
                try:
                    for target in _tree([_identity(psutil.Process(worker.pid))]):
                        targets[target.pid] = target
                except psutil.NoSuchProcess:
                    pass
                except (psutil.Error, ProcessActionError) as exc:
                    warnings.append(f'worker {worker.pid}: {exc}')
        for manager, original in reversed(self.sessions):
            try:
                snapshot = manager.navigator.snapshot([])
                if snapshot.get('error'):
                    raise SessionError(snapshot['error'])
                if not any(p['session_id'] == original.session_id for p in snapshot['panes']):
                    continue  # Already closed; never select a substitute by name.
                for attempt in range(3):
                    latest = manager.prepare_removal({'session_id': original.session_id, 'session': original.name})
                    if latest.fingerprint[0] != original.fingerprint[0]:
                        raise SessionError('Server or session identity changed; cleanup refused')
                    try:
                        roots = [_identity(psutil.Process(p['pane_pid'])) for p in latest.panes]
                        descendants = _tree(roots)
                        break
                    except (psutil.NoSuchProcess, ProcessActionError):
                        # session_exec and completed demo workers may exec their
                        # shell during startup/exit. Re-read this owned session.
                        if attempt == 2:
                            raise
                # This exact session was created by this demo invocation. The
                # user's demo-exit policy authorizes its removal without typing.
                manager.remove(latest, confirmation=latest.name)
                targets.update((target.pid, target) for target in descendants)
            except (SessionError, psutil.Error, ProcessActionError) as exc:
                warnings.append(f'session {original.name}: {exc}')

        signaled = []
        for target in targets.values():
            try:
                process = psutil.Process(target.pid)
                if process.create_time() != target.started:
                    raise ProcessActionError('PID identity changed; cleanup refused')
                # Ownership survives exec in a demo pane, but never PID reuse.
                # Capture and verify its current command before signaling it.
                process = _process(_identity(process))
                process.terminate()
                signaled.append(process)
            except psutil.NoSuchProcess:
                pass
            except (psutil.Error, ProcessActionError) as exc:
                warnings.append(f'PID {target.pid}: {exc}')
        # Reap our direct children, and bound the combined wait for all workers.
        deadline = time.monotonic() + 3
        for worker in self.workers:
            try:
                worker.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                warnings.append(f'worker {worker.pid} has not exited after SIGTERM')
        _, alive = psutil.wait_procs(signaled, timeout=max(0, deadline - time.monotonic()))
        for process in alive:
            warnings.append(f'PID {process.pid} has not exited after SIGTERM')
        for warning in dict.fromkeys(warnings):
            print('Demo cleanup: ' + warning, file=sys.stderr)
        if self.workers or self.sessions:
            print('Demo cleanup finished; results retained.' if not warnings else
                  'Demo cleanup incomplete; see warnings above. Results retained.')

    def __exit__(self, *exc):
        try:
            if self.cleanup:
                # Do not let a second interrupt abandon the remaining owned jobs.
                for number in self.handlers:
                    signal.signal(number, signal.SIG_IGN)
                self.close()
        finally:
            for number, handler in self.handlers.items():
                signal.signal(number, handler)
        return False
