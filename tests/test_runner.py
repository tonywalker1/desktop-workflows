import threading
import time
import unittest
from pathlib import Path

from dwf.launch import Context
from dwf.runner import execute
from dwf.task import parse_task
from helpers import FakeSystem, IsolatedTestCase


def command_task(mode, *commands):
    return parse_task({'version': 1, 'mode': mode,
                       'steps': [{'id': name, 'command': [name]} for name in commands]}, 't', Path('/x/t.toml'))


def failing(*names):
    return lambda argv: (1, 'boom\n') if argv[0] in names else (0, '')


class ExecuteTest(unittest.TestCase):
    def test_serial_stops_at_first_failure(self):
        system = FakeSystem()
        system.on_run = failing('b')
        results = execute(command_task('serial', 'a', 'b', 'c'), Context('t', system))
        self.assertEqual([r.status for r in results.values()], ['ok', 'failed', 'skipped'])
        self.assertEqual(results['b'].message, 'exited with status 1: boom')
        self.assertEqual(system.ran, [['a'], ['b']])

    def test_serial_runs_in_order(self):
        system = FakeSystem()
        execute(command_task('serial', 'a', 'b', 'c'), Context('t', system))
        self.assertEqual(system.ran, [['a'], ['b'], ['c']])

    def test_parallel_failure_does_not_stop_others(self):
        system = FakeSystem()
        system.on_run = failing('a')
        results = execute(command_task('parallel', 'a', 'b', 'c'), Context('t', system))
        self.assertEqual(list(results), ['a', 'b', 'c'])
        self.assertEqual([r.status for r in results.values()], ['failed', 'ok', 'ok'])

    def test_parallel_steps_overlap(self):
        system = FakeSystem()
        barrier = threading.Barrier(3, timeout=5)

        def rendezvous(argv):
            barrier.wait()      # deadlocks (and times out) unless all three run concurrently
            return 0, ''

        system.on_run = rendezvous
        t0 = time.monotonic()
        results = execute(command_task('parallel', 'a', 'b', 'c'), Context('t', system))
        self.assertTrue(all(r.status == 'ok' for r in results.values()), results)
        self.assertLess(time.monotonic() - t0, 5)

    def test_internal_error_is_contained(self):
        system = FakeSystem()

        def explode(argv):
            if argv[0] == 'a':
                raise RuntimeError('bug')
            return 0, ''

        system.on_run = explode
        results = execute(command_task('parallel', 'a', 'b'), Context('t', system))
        self.assertEqual(results['a'].status, 'failed')
        self.assertIn('internal error', results['a'].message)
        self.assertEqual(results['b'].status, 'ok')


class AppStepTest(IsolatedTestCase):
    def test_app_uses_gio_in_own_scope(self):
        path = self.desktop_file('org.example.App', 'example %U')
        system = FakeSystem()
        task = parse_task({'version': 1, 'steps': [{'id': 'x', 'app': 'org.example.App'}]}, 't', Path('/t.toml'))
        results = execute(task, Context('t', system))
        self.assertEqual(results['x'].status, 'ok')
        argv, unit = system.spawned[0]
        self.assertEqual(argv, ['gio', 'launch', str(path)])
        self.assertRegex(unit, r'^app-dwf-t-x-[0-9a-f]{8}\.scope$')

    def test_app_without_gio_runs_exec(self):
        self.desktop_file('org.example.App', 'example --flag %U')
        system = FakeSystem(tools=())
        task = parse_task({'version': 1, 'steps': [{'id': 'x', 'app': 'org.example.App'}]}, 't', Path('/t.toml'))
        execute(task, Context('t', system))
        self.assertEqual(system.spawned[0][0], ['example', '--flag'])

    def test_missing_app_fails(self):
        task = parse_task({'version': 1, 'steps': [{'id': 'x', 'app': 'nope'}]}, 't', Path('/t.toml'))
        results = execute(task, Context('t', FakeSystem()))
        self.assertEqual(results['x'].status, 'failed')
        self.assertIn('no desktop entry nope.desktop', results['x'].message)


if __name__ == '__main__':
    unittest.main()
