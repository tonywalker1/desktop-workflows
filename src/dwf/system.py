"""Everything dwf does to the outside world: spawning processes, systemd scopes, session-bus names, time.

Launch code talks only to a System, so tests substitute a fake and `dwf run --dry-run` substitutes DryRunSystem.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger('dwf')


class Proc(Protocol):
    def poll(self) -> int | None: ...

    def wait(self, timeout: float) -> int | None:
        """Exit status, or None if still running after `timeout` seconds."""


class _RealProc:
    def __init__(self, popen: subprocess.Popen):
        self._popen = popen

    def poll(self) -> int | None:
        return self._popen.poll()

    def wait(self, timeout: float) -> int | None:
        try:
            return self._popen.wait(timeout)
        except subprocess.TimeoutExpired:
            return None


class _DoneProc:
    def __init__(self, rc: int = 0):
        self.rc = rc

    def poll(self) -> int | None:
        return self.rc

    def wait(self, timeout: float) -> int | None:
        return self.rc


class System:
    def __init__(self):
        self._scopes: bool | None = None

    # --- queries ---

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def scopes_available(self) -> bool:
        """True when a systemd user manager can create transient scopes."""
        if self._scopes is None:
            runtime = os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
            self._scopes = (Path('/run/systemd/system').is_dir() and Path(runtime, 'systemd', 'private').exists()
                            and self.which('systemd-run') is not None)
        return self._scopes

    def bus_names(self) -> set[str] | None:
        """Names on the session bus, or None if they cannot be listed."""
        if not self.which('gdbus'):
            return None
        try:
            out = subprocess.run(
                ['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus', '--object-path',
                 '/org/freedesktop/DBus', '--method', 'org.freedesktop.DBus.ListNames'],
                capture_output=True, text=True, timeout=5, check=True).stdout
        except (OSError, subprocess.SubprocessError) as e:
            log.debug('cannot list bus names: %s', e)
            return None
        return set(re.findall(r"'([^']*)'", out))

    # --- actions ---

    def spawn(self, argv: list[str], unit: str | None = None, env: dict | None = None, cwd: str | None = None,
              label: str = '') -> Proc:
        """Start a process detached from dwf (own session; own systemd scope when `unit` is given and available)."""
        full = self._scoped(argv, unit)
        log.debug('%s: spawn %s', label, shlex.join(full))
        popen = subprocess.Popen(full, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True, env=env, cwd=cwd)
        return _RealProc(popen)

    def run(self, argv: list[str], timeout: float | None = None, env: dict | None = None, cwd: str | None = None,
            label: str = '') -> tuple[int, str]:
        """Run to completion; return (exit status, combined stdout+stderr). Raises TimeoutError."""
        log.debug('%s: run %s', label, shlex.join(argv))
        try:
            done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, errors='replace', timeout=timeout,
                                  env=env, cwd=cwd)
        except subprocess.TimeoutExpired:
            raise TimeoutError(f'timed out after {timeout:g} s') from None
        return done.returncode, done.stdout

    def wait_until(self, predicate: Callable[[], bool], timeout: float, interval: float = 0.1) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(interval)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()

    def _scoped(self, argv: list[str], unit: str | None) -> list[str]:
        if unit is None or not self.scopes_available():
            return list(argv)
        # The scope keeps the app out of dwf's own cgroup (and out of whatever launched dwf), so it is accounted,
        # limited and OOM-killed as itself. systemd-run execs the command, so exit status passes through.
        return ['systemd-run', '--user', '--scope', '--quiet', '--collect', '--slice=app.slice',
                f'--unit={unit}', '--', *argv]


class DryRunSystem(System):
    """Reads real state (bus names, files) but only reports what it would run."""

    def __init__(self, report: Callable[[str], None] = print):
        super().__init__()
        self.report = report
        self.commands: list[list[str]] = []

    def spawn(self, argv, unit=None, env=None, cwd=None, label=''):
        self.commands.append(list(argv))
        self.report(f'{label}: would start  {shlex.join(argv)}' + ('  [own scope]' if unit else ''))
        return _DoneProc(0)

    def run(self, argv, timeout=None, env=None, cwd=None, label=''):
        self.commands.append(list(argv))
        self.report(f'{label}: would run    {shlex.join(argv)}')
        return 0, ''

    def wait_until(self, predicate, timeout, interval=0.1):
        return True

    def sleep(self, seconds):
        pass
