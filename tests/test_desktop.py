import shutil
import subprocess
import unittest
from pathlib import Path

from dwf import desktop
from dwf.desktop import (DesktopEntryError, find_app, join_exec, split_exec, strip_field_codes, unescape_value)
from dwf.task import parse_task
from helpers import FLATPAK_FIREFOX_BASE, FLATPAK_FIREFOX_EXEC, IsolatedTestCase


class ExecSyntaxTest(unittest.TestCase):
    def test_split_plain_and_quoted(self):
        self.assertEqual(split_exec('foo --bar "a b" c'), ['foo', '--bar', 'a b', 'c'])

    def test_split_escapes_inside_quotes(self):
        self.assertEqual(split_exec(r'x "a\"b\`c\$d\\e"'), ['x', 'a"b`c$d\\e'])

    def test_literal_backslash_needs_four_in_file(self):
        # The string escape rule runs first, then the Exec quoting rule.
        self.assertEqual(split_exec(unescape_value(r'x "a\\\\b"')), ['x', 'a\\b'])

    def test_unterminated_quote(self):
        with self.assertRaises(DesktopEntryError):
            split_exec('x "oops')

    def test_join_round_trips(self):
        cases = [
            ['/home/a b/.local/bin/dwf', 'run', 'comms'],
            ['dwf', 'a"b', 'c$d', 'e\\f', '100%', "it's", ''],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                value = join_exec(argv)
                self.assertEqual(strip_field_codes(split_exec(unescape_value(value))), argv)

    def test_field_codes(self):
        self.assertEqual(strip_field_codes(['app', '%U', '--x=%f', '100%%', '%i']), ['app', '--x=', '100%'])

    def test_unescape(self):
        self.assertEqual(unescape_value(r'a\sb\nc\\d\;'), 'a b\nc\\d\\;')


class FindAppTest(IsolatedTestCase):
    def test_flatpak_launch_argv_drops_forwarding_markers(self):
        self.desktop_file('org.mozilla.firefox', FLATPAK_FIREFOX_EXEC, 'X-Flatpak=org.mozilla.firefox')
        app = find_app('org.mozilla.firefox.desktop')
        self.assertEqual(app.launch_argv(), FLATPAK_FIREFOX_BASE)
        self.assertEqual(app.flatpak_id, 'org.mozilla.firefox')

    def test_user_entry_overrides_system_and_hidden_masks(self):
        self.desktop_file('foo', 'sys-foo')
        self.assertEqual(find_app('foo').launch_argv(), ['sys-foo'])
        self.desktop_file('foo', 'user-foo', system=False)
        self.assertEqual(find_app('foo').launch_argv(), ['user-foo'])
        self.desktop_file('foo', 'user-foo', 'Hidden=true', system=False)
        self.assertIsNone(find_app('foo'))

    def test_missing(self):
        self.assertIsNone(find_app('nope'))


def _task(task_id='comms', name='Comms; Mail & Web', path='/x/comms.toml'):
    return parse_task({'version': 1, 'name': name, 'description': 'Line one\nline two', 'keywords': ['a;b', 'c'],
                       'steps': [{'app': 'x'}]}, task_id, Path(path))


class EntryGenerationTest(IsolatedTestCase):
    def test_render_parses_back(self):
        text = desktop.render_entry(_task(), '/home/a b/.local/bin/dwf')
        path = self.write(self.root / 'e.desktop', text)
        entry = desktop.read_entry(path)
        self.assertEqual(unescape_value(entry['Name']), 'Comms; Mail & Web')
        self.assertEqual(split_exec(unescape_value(entry['Exec'])), ['/home/a b/.local/bin/dwf', 'run', 'comms'])
        self.assertEqual(entry['StartupNotify'], 'false')
        self.assertEqual(entry[desktop.MANAGED_KEY], 'true')

    @unittest.skipUnless(shutil.which('desktop-file-validate'), 'desktop-file-validate not installed')
    def test_render_validates(self):
        path = self.write(self.root / 'dwf-comms.desktop', desktop.render_entry(_task(), '/usr/bin/dwf'))
        done = subprocess.run(['desktop-file-validate', str(path)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_sync_writes_skips_prunes_and_keeps_invalid(self):
        apps = self.root / 'apps'
        first = desktop.sync_entries([_task('a', path='/t/a.toml'), _task('b', path='/t/b.toml')], '/bin/dwf',
                                     apps_dir=apps)
        self.assertEqual(sorted(p.name for p in first.written), ['dwf-a.desktop', 'dwf-b.desktop'])
        (apps / 'dwf-foreign.desktop').write_text('[Desktop Entry]\nName=not ours\n')
        second = desktop.sync_entries([_task('a', path='/t/a.toml')], '/bin/dwf', keep={'b'}, apps_dir=apps)
        self.assertEqual([p.name for p in second.unchanged], ['dwf-a.desktop'])
        self.assertEqual(second.removed, [])
        third = desktop.sync_entries([_task('a', path='/t/a.toml')], '/bin/dwf', apps_dir=apps)
        self.assertEqual([p.name for p in third.removed], ['dwf-b.desktop'])
        self.assertTrue((apps / 'dwf-foreign.desktop').exists())
        self.assertEqual([p.name for p in desktop.remove_all_entries(apps)], ['dwf-a.desktop'])


if __name__ == '__main__':
    unittest.main()
