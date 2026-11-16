import contextlib
import io
import os
import subprocess
import sys
import unittest
import zipapp
from pathlib import Path

from dwf import desktop
from dwf.cli import main
from dwf.notify import notify_argv
from helpers import IsolatedTestCase

SRC = Path(__file__).resolve().parent.parent / 'src'


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(list(argv))
    return rc, out.getvalue(), err.getvalue()


class CliTest(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.task_file('good', '''
            version = 1
            name = "Good"
            mode = "serial"
            [[steps]]
            id = "first"
            command = ["true"]
            [[steps]]
            id = "second"
            command = ["sh", "-c", "echo nope >&2; exit 3"]
            [[steps]]
            id = "third"
            command = ["true"]
            ''')

    def test_run_real_commands_serial(self):
        rc, out, _ = run_cli('run', '--notify', 'never', 'good')
        self.assertEqual(rc, 1)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith('ok      first'))
        self.assertIn('failed  second  exited with status 3: nope', lines[1])
        self.assertTrue(lines[2].startswith('skipped third'))
        self.assertTrue((self.home / '.local' / 'state' / 'dwf' / 'dwf.log').is_file())

    def test_dry_run_runs_nothing(self):
        rc, out, _ = run_cli('run', '--dry-run', 'good')
        self.assertEqual(rc, 0)
        self.assertIn("second: would run    sh -c 'echo nope >&2; exit 3'", out)

    def test_run_missing_task(self):
        rc, _, err = run_cli('run', '--notify', 'never', 'missing')
        self.assertEqual(rc, 2)
        self.assertIn('no such task file', err)

    def test_sync_list_and_remove(self):
        self.task_file('broken', 'version = 1\n')
        rc, out, err = run_cli('sync', '--exec', '/opt/dwf/bin/dwf')
        self.assertEqual(rc, 1)                     # one invalid task
        self.assertIn('dwf-good.desktop', out)
        self.assertIn('broken.toml', err)
        entry = desktop.read_entry(desktop.entry_path('good'))
        self.assertEqual(entry['Exec'], '/opt/dwf/bin/dwf run good')
        rc, out, _ = run_cli('list')
        self.assertIn('good                 Good', out)
        self.assertIn('broken               INVALID', out)
        rc, out, _ = run_cli('sync', '--remove-all')
        self.assertEqual(rc, 0)
        self.assertFalse(desktop.entry_path('good').exists())

    def test_check_reports_missing_app_and_command(self):
        self.task_file('needs', '''
            version = 1
            [[steps]]
            app = "org.example.Missing"
            [[steps]]
            command = ["definitely-not-a-command-dwf"]
            ''')
        rc, out, err = run_cli('check', 'needs')
        self.assertEqual(rc, 1)
        self.assertIn('app1: no desktop entry org.example.Missing.desktop', err)
        self.assertIn('command2: definitely-not-a-command-dwf not found on PATH', err)
        rc, out, _ = run_cli('check', 'good')
        self.assertEqual((rc, out.strip()), (0, 'good: ok'))


class ZipappTest(IsolatedTestCase):
    def test_exit_status_survives_zipapp(self):
        # Same archive layout as scripts/install.sh builds.
        target = self.root / 'dwf'
        zipapp.create_archive(SRC, target, interpreter=sys.executable, main='dwf.cli:entry',
                              filter=lambda p: '__pycache__' not in p.parts)
        self.task_file('fails', 'version = 1\n[[steps]]\ncommand = ["false"]\n')
        done = subprocess.run([sys.executable, str(target), 'run', '--notify', 'never', 'fails'],
                              capture_output=True, text=True, env=os.environ)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)


class NotifyArgvTest(unittest.TestCase):
    def test_no_argument_looks_like_an_option(self):
        argv = notify_argv("it's -1", 'line\nbreak', 'dialog-error', True)
        args = argv[argv.index('org.freedesktop.Notifications.Notify') + 1:]
        self.assertEqual(len(args), 8)
        self.assertFalse(any(a.startswith('-') for a in args), args)
        self.assertEqual(args[3], r"'it\'s -1'")
        self.assertEqual(args[4], r"'line\nbreak'")


if __name__ == '__main__':
    unittest.main()
