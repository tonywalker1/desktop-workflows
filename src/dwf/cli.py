"""Command-line interface: dwf run | list | check | edit | sync."""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from dwf import __version__, desktop, notify, paths
from dwf.launch import Context, preflight
from dwf.runner import execute
from dwf.system import DryRunSystem, System
from dwf.task import TaskError, find_task, is_valid_task_id, load_task, load_tasks, task_path, template

log = logging.getLogger('dwf')

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def _print_task_error(e: TaskError) -> None:
    print(f'{e.path}:', file=sys.stderr)
    for problem in e.problems:
        print(f'  {problem}', file=sys.stderr)


def self_exec_path() -> str | None:
    """Absolute path of the dwf executable, for generated Exec lines."""
    argv0 = Path(sys.argv[0])
    if argv0.suffix != '.py' and argv0.name != '__main__.py':
        resolved = argv0 if argv0.is_absolute() or '/' in sys.argv[0] else Path(shutil.which(sys.argv[0]) or '')
        if resolved.is_file():
            return str(resolved.absolute())
    found = shutil.which('dwf')
    return str(Path(found).absolute()) if found else None


# --- commands --------------------------------------------------------------------------------------------------------

def cmd_run(args) -> int:
    try:
        task = find_task(args.task)
    except TaskError as e:
        _print_task_error(e)
        if args.notify != 'never':
            notify.notify(f'dwf: cannot run {args.task}', '\n'.join(e.problems))
        return EXIT_USAGE
    system = DryRunSystem() if args.dry_run else System()
    log.info('run %s (%s, %d steps)%s', task.id, task.mode, len(task.steps), ' [dry run]' if args.dry_run else '')
    results = execute(task, Context(task.id, system))

    width = max(len(sid) for sid in results)
    for sid, r in results.items():
        print(f'{r.status:<8}{sid:<{width + 2}}{r.message} ({r.seconds:.1f} s)')
    failed = {sid: r for sid, r in results.items() if r.status != 'ok'}
    if args.dry_run:
        return EXIT_OK if not failed else EXIT_FAILED
    if failed and args.notify != 'never':
        body = '\n'.join(f'{sid}: {r.status}: {r.message}' for sid, r in failed.items())
        notify.notify(f'{task.name}: {len(failed)} of {len(results)} steps did not succeed', body)
    elif not failed and args.notify == 'always':
        notify.notify(task.name, f'{len(results)} steps started', icon='emblem-ok-symbolic', critical=False)
    return EXIT_FAILED if failed else EXIT_OK


def cmd_list(args) -> int:
    tasks, errors = load_tasks()
    entries = desktop.managed_entries()
    if not tasks and not errors:
        print(f'no tasks in {paths.tasks_dir()} (create one with `dwf edit <name>`)')
        return EXIT_OK
    for task in tasks:
        launcher = 'launcher' if task.id in entries else 'no launcher (run dwf sync)'
        print(f'{task.id:<20} {task.name:<28} {len(task.steps)} steps, {task.mode}, {launcher}')
    for e in errors:
        print(f'{e.path.stem:<20} INVALID: {e.problems[0]}' + (' ...' if len(e.problems) > 1 else ''))
    return EXIT_FAILED if errors else EXIT_OK


def cmd_check(args) -> int:
    if args.tasks:
        tasks, errors = [], []
        for task_id in args.tasks:
            try:
                tasks.append(find_task(task_id))
            except TaskError as e:
                errors.append(e)
    else:
        tasks, errors = load_tasks()
    for e in errors:
        _print_task_error(e)
    bad = bool(errors)
    for task in tasks:
        problems = [f'{step.id}: {p}' for step in task.steps for p in preflight(step)]
        if problems:
            bad = True
            print(f'{task.path}:', file=sys.stderr)
            for p in problems:
                print(f'  {p}', file=sys.stderr)
        else:
            print(f'{task.id}: ok')
    return EXIT_FAILED if bad else EXIT_OK


def _editor() -> list[str] | None:
    for var in ('VISUAL', 'EDITOR'):
        if os.environ.get(var):
            return shlex.split(os.environ[var])
    for candidate in ('nano', 'vi'):
        if shutil.which(candidate):
            return [candidate]
    return None


def cmd_edit(args) -> int:
    if not is_valid_task_id(args.task):
        print(f'invalid task name {args.task!r}: use lowercase letters, digits and dashes', file=sys.stderr)
        return EXIT_USAGE
    editor = _editor()
    if editor is None or not sys.stdin.isatty():
        print('dwf edit needs a terminal and $VISUAL or $EDITOR', file=sys.stderr)
        return EXIT_USAGE
    path = task_path(args.task)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(template(args.task), encoding='utf-8')
        print(f'created {path} from the template')
    while True:
        subprocess.run([*editor, str(path)])
        try:
            load_task(path)
            break
        except TaskError as e:
            _print_task_error(e)
            answer = input('[e]dit again or [k]eep as is? ').strip().lower()
            if not answer.startswith('e'):
                return EXIT_FAILED
    return cmd_sync(argparse.Namespace(exec=None, remove_all=False))


def cmd_sync(args) -> int:
    if args.remove_all:
        for path in desktop.remove_all_entries():
            print(f'removed {path}')
        return EXIT_OK
    exec_path = args.exec or self_exec_path()
    if exec_path is None:
        print('cannot find the dwf executable for launcher entries; install dwf or pass --exec', file=sys.stderr)
        return EXIT_USAGE
    tasks, errors = load_tasks()
    for e in errors:
        _print_task_error(e)
    result = desktop.sync_entries(tasks, exec_path, keep={e.path.stem for e in errors})
    for path in result.written:
        print(f'wrote   {path}')
    for path in result.removed:
        print(f'removed {path}')
    if errors:
        print(f'{len(errors)} invalid task(s) left unchanged', file=sys.stderr)
    return EXIT_FAILED if errors else EXIT_OK


# --- entry point -----------------------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='dwf', description='Launch desktop workflows.')
    parser.add_argument('--version', action='version', version=f'dwf {__version__}')
    parser.add_argument('-v', '--verbose', action='store_true', help='log details to stderr')
    sub = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')

    p = sub.add_parser('run', help='run a task')
    p.add_argument('task')
    p.add_argument('-n', '--dry-run', action='store_true', help='show what would run; launch nothing')
    p.add_argument('--notify', choices=('auto', 'always', 'never'), default='auto',
                   help='desktop notification: on failure (auto), always, or never')
    p.set_defaults(func=cmd_run)

    p = sub.add_parser('list', help='list tasks')
    p.set_defaults(func=cmd_list)

    p = sub.add_parser('check', help='validate tasks and check that their apps and profiles exist')
    p.add_argument('tasks', nargs='*', metavar='task')
    p.set_defaults(func=cmd_check)

    p = sub.add_parser('edit', help='create or edit a task in $VISUAL/$EDITOR, then sync launchers')
    p.add_argument('task')
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser('sync', help='write launcher (.desktop) entries for all tasks; remove stale ones')
    p.add_argument('--exec', metavar='PATH', help='dwf executable to put in Exec= (default: this one)')
    p.add_argument('--remove-all', action='store_true', help='remove every launcher entry dwf created')
    p.set_defaults(func=cmd_sync)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    notify.setup_logging(verbose=args.verbose, to_file=args.command == 'run')
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


def entry() -> None:
    """Console entry point. zipapp's generated __main__ discards main()'s return value, so exit here."""
    sys.exit(main())
