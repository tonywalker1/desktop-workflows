import unittest
from pathlib import Path

from dwf.task import AppStep, BrowserStep, CommandStep, TaskError, find_task, load_task, parse_task, template
from helpers import IsolatedTestCase


def problems(data, task_id='t'):
    with unittest.TestCase().assertRaises(TaskError) as cm:
        parse_task(data, task_id, Path(f'/x/{task_id}.toml'))
    return '\n'.join(cm.exception.problems)


class ParseTest(unittest.TestCase):
    def test_full_task_and_defaults(self):
        task = parse_task({
            'version': 1,
            'steps': [
                {'app': 'org.mozilla.thunderbird'},
                {'id': 'web', 'browser': 'org.mozilla.firefox', 'urls': ['https://a', 'about:blank'],
                 'profile': 'work'},
                {'command': ['true'], 'timeout': 5},
            ],
        }, 'comms', Path('/x/comms.toml'))
        self.assertEqual((task.name, task.mode, task.description), ('comms', 'parallel', ''))
        self.assertEqual(task.steps[0], AppStep('app1', 'org.mozilla.thunderbird'))
        self.assertEqual(task.steps[1], BrowserStep('web', 'org.mozilla.firefox', ('https://a', 'about:blank'),
                                                    profile='work'))
        self.assertEqual(task.steps[2], CommandStep('command3', ('true',), timeout=5.0))

    def test_errors(self):
        cases = {
            "'version' is required": {'steps': [{'app': 'x'}]},
            'unsupported version': {'version': 2, 'steps': [{'app': 'x'}]},
            "unknown key 'step'": {'version': 1, 'step': [], 'steps': [{'app': 'x'}]},
            'needs exactly one of': {'version': 1, 'steps': [{'app': 'x', 'command': ['y']}]},
            'not a URL with a scheme': {'version': 1, 'steps': [{'browser': 'b', 'urls': ['--remote-debugging']}]},
            "'urls' must be a non-empty list": {'version': 1, 'steps': [{'browser': 'b', 'urls': []}]},
            'duplicate step id': {'version': 1, 'steps': [{'id': 'a', 'app': 'x'}, {'id': 'a', 'app': 'y'}]},
            'timeout must be a positive': {'version': 1, 'steps': [{'command': ['x'], 'timeout': True}]},
            'mode must be one of': {'version': 1, 'mode': 'random', 'steps': [{'app': 'x'}]},
            'at least one [[steps]]': {'version': 1, 'steps': []},
            'family must be one of': {'version': 1, 'steps': [{'browser': 'b', 'urls': ['https://a'],
                                                               'family': 'webkit'}]},
        }
        for expected, data in cases.items():
            with self.subTest(expected=expected):
                self.assertIn(expected, problems(data))

    def test_bad_task_id(self):
        self.assertIn('task IDs must match', problems({'version': 1, 'steps': [{'app': 'x'}]}, task_id='Bad_Id'))

    def test_template_is_valid(self):
        task = parse_task(__import__('tomllib').loads(template('my-task')), 'my-task', Path('/x/my-task.toml'))
        self.assertEqual(task.name, 'My Task')


class LoadTest(IsolatedTestCase):
    def test_toml_syntax_error(self):
        path = self.task_file('broken', 'version = \n')
        with self.assertRaises(TaskError) as cm:
            load_task(path)
        self.assertIn('TOML syntax error', cm.exception.problems[0])

    def test_find_task(self):
        self.task_file('ok', 'version = 1\n[[steps]]\napp = "x"\n')
        self.assertEqual(find_task('ok').id, 'ok')
        with self.assertRaises(TaskError):
            find_task('../etc/passwd')
        with self.assertRaises(TaskError):
            find_task('missing')


if __name__ == '__main__':
    unittest.main()
