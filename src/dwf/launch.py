"""Running individual steps."""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import threading
from collections import defaultdict
from pathlib import Path

from dwf import browsers
from dwf.desktop import DesktopApp, DesktopEntryError, find_app
from dwf.errors import StepFailed
from dwf.task import AppStep, BrowserStep, CommandStep, Step

log = logging.getLogger('dwf')

APP_LAUNCH_TIMEOUT_S = 10.0
APP_FALLBACK_GRACE_S = 1.0
OUTPUT_LOG_LINES = 50


class Context:
    """Shared state for one `dwf run`: the System, and per-browser-instance serialization."""

    def __init__(self, task_id: str, system):
        self.task_id = task_id
        self.system = system
        self._guard = threading.Lock()
        self._locks: dict[tuple, threading.Lock] = defaultdict(threading.Lock)
        self._cold_started: dict[tuple, float] = {}

    def lock(self, key: tuple) -> threading.Lock:
        """Steps that target the same browser instance must not interleave their launcher calls."""
        with self._guard:
            return self._locks[key]

    def mark_cold_start(self, key: tuple) -> None:
        self._cold_started[key] = self.system.monotonic()

    def settle(self, key: tuple, seconds: float) -> None:
        started = self._cold_started.get(key)
        if started is not None:
            remaining = started + seconds - self.system.monotonic()
            if remaining > 0:
                self.system.sleep(remaining)

    def unit(self, step_id: str) -> str:
        return f'app-dwf-{self.task_id}-{step_id}-{secrets.token_hex(4)}.scope'


def resolve_app(app_id: str) -> DesktopApp:
    app = find_app(app_id)
    if app is None:
        raise StepFailed(f'no desktop entry {app_id}.desktop on XDG_DATA_DIRS')
    return app


def run_step(step: Step, ctx: Context) -> str:
    """Run one step; return a short success message or raise StepFailed."""
    try:
        if isinstance(step, AppStep):
            return _launch_app(step, ctx)
        if isinstance(step, BrowserStep):
            app = resolve_app(step.browser)
            if browsers.family_of(app, step.family) == 'firefox':
                return browsers.launch_firefox(step, app, ctx)
            return browsers.launch_chromium(step, app, ctx)
        if isinstance(step, CommandStep):
            return _run_command(step, ctx)
    except DesktopEntryError as e:
        raise StepFailed(str(e)) from None
    except OSError as e:
        raise StepFailed(f'{e.strerror or e}: {e.filename or ""}'.rstrip(': ')) from None
    raise TypeError(f'unknown step type {type(step).__name__}')


def _launch_app(step: AppStep, ctx: Context) -> str:
    app = resolve_app(step.app)
    if ctx.system.which('gio'):
        # gio handles DBusActivatable apps and Exec field codes the same way the desktop shell does.
        proc = ctx.system.spawn(['gio', 'launch', str(app.path)], unit=ctx.unit(step.id), label=step.id)
        rc = proc.wait(APP_LAUNCH_TIMEOUT_S)
        if rc not in (0, None):
            raise StepFailed(f'gio launch {app.path.name} exited with status {rc}')
    else:
        proc = ctx.system.spawn(app.launch_argv(), unit=ctx.unit(step.id), label=step.id)
        rc = proc.wait(APP_FALLBACK_GRACE_S)
        if rc not in (0, None):
            raise StepFailed(f'{app.name} exited with status {rc}')
    return f'launched {app.name}'


def _run_command(step: CommandStep, ctx: Context) -> str:
    env = {**os.environ, 'DWF_TASK': ctx.task_id, 'DWF_STEP': step.id}
    cwd = os.path.expanduser(step.cwd) if step.cwd else None
    try:
        rc, output = ctx.system.run(list(step.command), timeout=step.timeout, env=env, cwd=cwd, label=step.id)
    except TimeoutError as e:
        raise StepFailed(str(e)) from None
    lines = output.splitlines()
    for line in lines[-OUTPUT_LOG_LINES:]:
        log.debug('%s| %s', step.id, line)
    if rc != 0:
        last = next((line for line in reversed(lines) if line.strip()), '')
        raise StepFailed(f'exited with status {rc}' + (f': {last.strip()}' if last else ''))
    return 'completed'


def preflight(step: Step, which=None) -> list[str]:
    """Problems that would make the step fail on this machine, found without launching anything."""
    which = which or shutil.which
    try:
        if isinstance(step, AppStep):
            resolve_app(step.app)
        elif isinstance(step, BrowserStep):
            app = resolve_app(step.browser)
            app.launch_argv()
            if browsers.family_of(app, step.family) == 'firefox':
                browsers.resolve_firefox_profile(app, step.profile)
            elif step.profile:
                udd = browsers.chromium_user_data_dir(app, step.user_data_dir)
                browsers.resolve_chromium_profile(udd, step.profile, app.id)
        elif isinstance(step, CommandStep):
            exe = step.command[0]
            if '/' in exe:
                path = Path(os.path.expanduser(exe))
                if not (path.is_file() and os.access(path, os.X_OK)):
                    return [f'{exe} is not an executable file']
            elif which(exe) is None:
                return [f'{exe} not found on PATH']
    except (StepFailed, DesktopEntryError) as e:
        return [str(e)]
    return []
