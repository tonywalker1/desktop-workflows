import base64
import json
import unittest
from pathlib import Path

from dwf import browsers
from dwf.browsers import remote_name_matches
from dwf.desktop import find_app
from dwf.errors import StepFailed
from dwf.launch import Context, run_step
from dwf.task import BrowserStep
from helpers import FLATPAK_CHROME_EXEC, FLATPAK_FIREFOX_BASE, FLATPAK_FIREFOX_EXEC, FakeSystem, IsolatedTestCase


def bus_name(path: str) -> str:
    return 'org.mozilla.firefox.' + base64.b64encode(path.encode()).decode().replace('=', '_')


class RemoteNameTest(unittest.TestCase):
    def test_observed_name(self):
        # Recorded from Firefox 160 (Flatpak) on the development machine.
        name = 'org.mozilla.firefox.L2hvbWUvdG9ueS8ubW96aWxsYS9maXJlZm94L2RhZDByYnd5LmRlZmF1bHQtZGVmYXVsdA__'
        self.assertTrue(remote_name_matches(name, '/home/tony/.mozilla/firefox/dad0rbwy.default-default'))
        self.assertFalse(remote_name_matches(name, '/home/tony/.mozilla/firefox/other.default'))
        self.assertFalse(remote_name_matches('org.gnome.Shell', '/x'))


class FirefoxTest(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.desktop_file('org.mozilla.firefox', FLATPAK_FIREFOX_EXEC, 'X-Flatpak=org.mozilla.firefox')
        root = self.home / '.var' / 'app' / 'org.mozilla.firefox' / '.mozilla' / 'firefox'
        self.write(root / 'profiles.ini', '''
            [Profile1]
            Name=work
            IsRelative=1
            Path=w0rk.work

            [Profile0]
            Name=tony
            IsRelative=1
            Path=abc.default-default

            [InstallAAA]
            Default=abc.default-default
            ''')
        self.write(root / 'installs.ini', '[AAA]\nDefault=abc.default-default\n[BBB]\nDefault=abc.default-default\n')
        # Paths as the sandboxed browser sees them.
        self.default_path = str(self.home / '.mozilla' / 'firefox' / 'abc.default-default')
        self.work_path = str(self.home / '.mozilla' / 'firefox' / 'w0rk.work')
        self.system = FakeSystem()
        self.ctx = Context('t', self.system)

    def step(self, urls=('https://a', 'https://b', 'https://c'), profile=None, sid='web'):
        return BrowserStep(sid, 'org.mozilla.firefox', tuple(urls), profile=profile)

    def test_warm_default_profile_opens_window_then_tabs(self):
        self.system.names = {bus_name(self.default_path)}
        message = run_step(self.step(), self.ctx)
        self.assertIn('running Firefox', message)
        self.assertEqual([argv for argv, _ in self.system.spawned], [
            FLATPAK_FIREFOX_BASE + ['--new-window', 'https://a'],
            FLATPAK_FIREFOX_BASE + ['--new-tab', 'https://b'],
            FLATPAK_FIREFOX_BASE + ['--new-tab', 'https://c'],
        ])
        self.assertEqual(self.system.slept, 0)

    def test_cold_named_profile_single_call_then_settle(self):
        def start(argv):
            self.system.names.add(bus_name(self.work_path))
            return None             # the launcher is the browser
        self.system.on_spawn = start
        message = run_step(self.step(profile='WORK'), self.ctx)
        self.assertIn('started Firefox with 3 tab(s)', message)
        self.assertEqual(self.system.spawned[0][0],
                         FLATPAK_FIREFOX_BASE + ['--profile', self.work_path, 'https://a', 'https://b', 'https://c'])
        # A second window for the same instance right after its cold start waits for it to settle.
        self.system.on_spawn = lambda argv: 0
        run_step(self.step(urls=['https://d'], profile='work', sid='web2'), self.ctx)
        self.assertEqual(self.system.slept, browsers.FIREFOX_SETTLE_S)
        self.assertEqual(self.system.spawned[1][0][-2:], ['--new-window', 'https://d'])

    def test_cold_start_failure(self):
        self.system.on_spawn = lambda argv: 1
        with self.assertRaisesRegex(StepFailed, 'exited with status 1'):
            run_step(self.step(), self.ctx)

    def test_cold_start_timeout(self):
        self.system.on_spawn = lambda argv: None
        with self.assertRaisesRegex(StepFailed, 'did not start'):
            run_step(self.step(), self.ctx)

    def test_unknown_profile_lists_known(self):
        with self.assertRaisesRegex(StepFailed, r"'nope' not found .*known: tony, work"):
            run_step(self.step(profile='nope'), self.ctx)

    def test_absolute_profile_path(self):
        self.system.names = {bus_name('/srv/p')}
        run_step(self.step(urls=['https://a'], profile='/srv/p'), self.ctx)
        self.assertEqual(self.system.spawned[0][0][-4:], ['--profile', '/srv/p', '--new-window', 'https://a'])

    def test_legacy_root_preferred_over_xdg(self):
        xdg = self.home / '.var' / 'app' / 'org.mozilla.firefox' / 'config' / 'mozilla' / 'firefox'
        self.write(xdg / 'profiles.ini', '[Profile0]\nName=other\nIsRelative=1\nPath=o.other\nDefault=1\n')
        profile = browsers.resolve_firefox_profile(find_app('org.mozilla.firefox'), None)
        self.assertEqual(profile.path, self.default_path)
        self.assertFalse(profile.explicit)

    def test_unknown_running_state_assumes_warm(self):
        self.system.tools = {'gio'}         # no gdbus: bus names unavailable
        run_step(self.step(urls=['https://a']), self.ctx)
        self.assertEqual(self.system.spawned[0][0][-2:], ['--new-window', 'https://a'])

    def test_warm_launcher_failure(self):
        self.system.names = {bus_name(self.default_path)}
        self.system.on_spawn = lambda argv: 2
        with self.assertRaisesRegex(StepFailed, 'launcher exited with status 2'):
            run_step(self.step(), self.ctx)


class ChromiumTest(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.desktop_file('com.google.Chrome', FLATPAK_CHROME_EXEC, 'X-Flatpak=com.google.Chrome')
        udd = self.home / '.var' / 'app' / 'com.google.Chrome' / 'config' / 'google-chrome'
        udd.mkdir(parents=True)
        (udd / 'Local State').write_text(json.dumps({'profile': {'info_cache': {
            'Default': {'name': 'me@home.example'},
            'Profile 1': {'name': 'Work', 'user_name': 'me@work.example'},
        }}}))
        self.system = FakeSystem()
        self.ctx = Context('t', self.system)

    def step(self, profile=None):
        return BrowserStep('web', 'com.google.Chrome', ('https://a', 'https://b'), profile=profile)

    def test_profile_by_account_name_single_call(self):
        message = run_step(self.step(profile='me@work.example'), self.ctx)
        self.assertIn('running Google', message.replace('com.google.Chrome', 'Google'))
        argv = self.system.spawned[0][0]
        self.assertEqual(argv[-4:], ['--profile-directory=Profile 1', '--new-window', 'https://a', 'https://b'])
        self.assertNotIn('--file-forwarding', argv)

    def test_cold_start_is_success(self):
        self.system.on_spawn = lambda argv: None
        self.assertIn('started', run_step(self.step(profile='work'), self.ctx))

    def test_launcher_failure(self):
        self.system.on_spawn = lambda argv: 3
        with self.assertRaisesRegex(StepFailed, 'status 3'):
            run_step(self.step(), self.ctx)

    def test_unknown_profile(self):
        with self.assertRaisesRegex(StepFailed, r"'Play' not found .*Work \(Profile 1\)"):
            run_step(self.step(profile='Play'), self.ctx)


class FamilyTest(IsolatedTestCase):
    def test_unknown_family_needs_explicit(self):
        self.desktop_file('org.example.Browser', 'webbrowser %u')
        step = BrowserStep('web', 'org.example.Browser', ('https://a',))
        with self.assertRaisesRegex(StepFailed, 'set family'):
            run_step(step, Context('t', FakeSystem()))
        explicit = BrowserStep('web', 'org.example.Browser', ('https://a',), family='chromium')
        run_step(explicit, Context('t', FakeSystem()))


if __name__ == '__main__':
    unittest.main()
