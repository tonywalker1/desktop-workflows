"""Test doubles and an isolated HOME/XDG environment."""

import logging
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

# Keep expected warnings out of the test output (Python's last-resort handler prints them otherwise).
logging.getLogger('dwf').addHandler(logging.NullHandler())


class FakeProc:
    def __init__(self, rc):
        self.rc = rc

    def poll(self):
        return self.rc

    def wait(self, timeout):
        return self.rc


class FakeSystem:
    """Records calls. `on_spawn(argv)` returns the launcher's exit status (None = still running)."""

    def __init__(self, names=(), tools=('gio', 'gdbus')):
        self.names = set(names)
        self.tools = set(tools)
        self.spawned = []           # (argv, unit)
        self.ran = []               # argv
        self.on_spawn = lambda argv: 0
        self.on_run = lambda argv: (0, '')
        self.clock = 100.0
        self.slept = 0.0

    def which(self, name):
        return f'/usr/bin/{name}' if name in self.tools else None

    def scopes_available(self):
        return True

    def bus_names(self):
        return set(self.names) if 'gdbus' in self.tools else None

    def spawn(self, argv, unit=None, env=None, cwd=None, label=''):
        self.spawned.append((list(argv), unit))
        return FakeProc(self.on_spawn(list(argv)))

    def run(self, argv, timeout=None, env=None, cwd=None, label=''):
        self.ran.append(list(argv))
        return self.on_run(list(argv))

    def wait_until(self, predicate, timeout, interval=0.1):
        for _ in range(50):
            if predicate():
                return True
        return False

    def sleep(self, seconds):
        self.slept += seconds
        self.clock += seconds

    def monotonic(self):
        return self.clock


class IsolatedTestCase(unittest.TestCase):
    """HOME and the XDG directories point into a fresh temporary directory for each test."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.sysdata = self.root / 'usr' / 'share'
        env = {
            'HOME': str(self.home),
            'XDG_CONFIG_HOME': str(self.home / '.config'),
            'XDG_DATA_HOME': str(self.home / '.local' / 'share'),
            'XDG_STATE_HOME': str(self.home / '.local' / 'state'),
            'XDG_DATA_DIRS': str(self.sysdata),
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, path, text) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip(), encoding='utf-8')
        return path

    def desktop_file(self, app_id, exec_line, extra='', system=True) -> Path:
        base = self.sysdata if system else self.home / '.local' / 'share'
        return self.write(base / 'applications' / f'{app_id}.desktop',
                          f'[Desktop Entry]\nType=Application\nName={app_id}\nExec={exec_line}\n{extra}')

    def task_file(self, task_id, text) -> Path:
        return self.write(self.home / '.config' / 'dwf' / 'tasks' / f'{task_id}.toml', text)


FLATPAK_FIREFOX_EXEC = ('/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=firefox --file-forwarding '
                        'org.mozilla.firefox @@u %u @@')
FLATPAK_FIREFOX_BASE = ['/usr/bin/flatpak', 'run', '--branch=stable', '--arch=x86_64', '--command=firefox',
                        'org.mozilla.firefox']
FLATPAK_CHROME_EXEC = ('/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=/app/bin/chrome '
                       '--file-forwarding com.google.Chrome @@u %U @@')
