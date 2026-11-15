"""Task files: TOML schema (version 1), validation and loading.

A task lives in $XDG_CONFIG_HOME/dwf/tasks/<id>.toml; the file stem is the task ID. See README.md for the format.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Union

from dwf import paths

SCHEMA_VERSION = 1
TASK_ID_RE = re.compile(r'[a-z0-9][a-z0-9-]{0,62}')
STEP_ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,62}')
# A scheme is required, which also guarantees a URL can never be parsed as a browser option.
URL_RE = re.compile(r'[A-Za-z][A-Za-z0-9+.-]*:[^\s\x00-\x1f]*')
MODES = ('parallel', 'serial')
FAMILIES = ('firefox', 'chromium')

TOP_KEYS = {'version', 'name', 'description', 'icon', 'keywords', 'mode', 'steps'}
STEP_KEYS = {
    'app': {'id', 'app'},
    'browser': {'id', 'browser', 'urls', 'profile', 'family', 'user_data_dir'},
    'command': {'id', 'command', 'cwd', 'timeout'},
}


@dataclass(frozen=True)
class AppStep:
    id: str
    app: str


@dataclass(frozen=True)
class BrowserStep:
    id: str
    browser: str
    urls: tuple[str, ...]
    profile: str | None = None
    family: str | None = None
    user_data_dir: str | None = None


@dataclass(frozen=True)
class CommandStep:
    id: str
    command: tuple[str, ...]
    cwd: str | None = None
    timeout: float | None = None


Step = Union[AppStep, BrowserStep, CommandStep]


@dataclass(frozen=True)
class Task:
    id: str
    path: Path
    name: str
    description: str
    icon: str | None
    keywords: tuple[str, ...]
    mode: str
    steps: tuple[Step, ...]


class TaskError(Exception):
    def __init__(self, path: Path, problems: list[str]):
        self.path = Path(path)
        self.problems = list(problems)
        super().__init__(f'{path}: ' + '; '.join(problems))


def is_valid_task_id(task_id: str) -> bool:
    return bool(TASK_ID_RE.fullmatch(task_id))


def task_path(task_id: str) -> Path:
    return paths.tasks_dir() / f'{task_id}.toml'


# --- validation ------------------------------------------------------------------------------------------------------

class _Checker:
    def __init__(self):
        self.problems: list[str] = []

    def fail(self, where: str, msg: str) -> None:
        self.problems.append(f'{where}: {msg}' if where else msg)

    def unknown_keys(self, where: str, table: dict, allowed: set[str]) -> None:
        for key in sorted(set(table) - allowed):
            self.fail(where, f'unknown key {key!r}')

    def string(self, where: str, table: dict, key: str, required=False, default=None,
               allow_empty=False) -> str | None:
        value = table.get(key)
        if value is None:
            if required:
                self.fail(where, f'{key!r} is required')
            return default
        if not isinstance(value, str) or not (allow_empty or value.strip()):
            self.fail(where, f'{key!r} must be a {"" if allow_empty else "non-empty "}string')
            return default
        return value

    def string_list(self, where: str, table: dict, key: str, required=False) -> tuple[str, ...]:
        value = table.get(key)
        if value is None:
            if required:
                self.fail(where, f'{key!r} is required')
            return ()
        if (not isinstance(value, list) or (required and not value)
                or not all(isinstance(v, str) and v for v in value)):
            self.fail(where, f'{key!r} must be a {"non-empty " if required else ""}list of non-empty strings')
            return ()
        return tuple(value)


def _parse_step(c: _Checker, raw: Any, index: int, seen: set[str]) -> Step | None:
    where = f'steps[{index}]'
    if not isinstance(raw, dict):
        c.fail(where, 'must be a table ([[steps]])')
        return None
    kinds = [k for k in STEP_KEYS if k in raw]
    if len(kinds) != 1:
        c.fail(where, f'needs exactly one of {", ".join(repr(k) for k in STEP_KEYS)} (found {len(kinds)})')
        return None
    kind = kinds[0]
    step_id = raw.get('id', f'{kind}{index + 1}')
    if not isinstance(step_id, str) or not STEP_ID_RE.fullmatch(step_id):
        c.fail(where, f'id {step_id!r} must match {STEP_ID_RE.pattern}')
        return None
    where = f'steps[{index}] ({step_id})'
    if step_id in seen:
        c.fail(where, 'duplicate step id')
    seen.add(step_id)
    c.unknown_keys(where, raw, STEP_KEYS[kind])
    before = len(c.problems)

    if kind == 'app':
        step = AppStep(step_id, c.string(where, raw, 'app', required=True) or '')
    elif kind == 'browser':
        urls = c.string_list(where, raw, 'urls', required=True)
        for url in urls:
            if not URL_RE.fullmatch(url):
                c.fail(where, f'{url!r} is not a URL with a scheme (e.g. https://...)')
        family = c.string(where, raw, 'family')
        if family is not None and family not in FAMILIES:
            c.fail(where, f'family must be one of {", ".join(FAMILIES)}')
        step = BrowserStep(step_id, c.string(where, raw, 'browser', required=True) or '', urls,
                           profile=c.string(where, raw, 'profile'), family=family,
                           user_data_dir=c.string(where, raw, 'user_data_dir'))
    else:
        timeout = raw.get('timeout')
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                    or timeout <= 0):
            c.fail(where, 'timeout must be a positive number of seconds')
            timeout = None
        step = CommandStep(step_id, c.string_list(where, raw, 'command', required=True),
                           cwd=c.string(where, raw, 'cwd'), timeout=float(timeout) if timeout else None)
    return step if len(c.problems) == before else None


def parse_task(data: dict, task_id: str, path: Path) -> Task:
    c = _Checker()
    if not is_valid_task_id(task_id):
        c.fail('', f'file name {path.name!r}: task IDs must match {TASK_ID_RE.pattern}')
    c.unknown_keys('', data, TOP_KEYS)
    version = data.get('version')
    if version is None:
        c.fail('', f"'version' is required (current: version = {SCHEMA_VERSION})")
    elif isinstance(version, bool) or version != SCHEMA_VERSION:
        c.fail('', f'unsupported version {version!r} (this dwf supports {SCHEMA_VERSION})')
    name = c.string('', data, 'name', default=task_id)
    description = c.string('', data, 'description', default='', allow_empty=True)
    icon = c.string('', data, 'icon')
    keywords = c.string_list('', data, 'keywords')
    mode = c.string('', data, 'mode', default='parallel')
    if mode not in MODES:
        c.fail('', f'mode must be one of {", ".join(MODES)}')
    raw_steps = data.get('steps')
    steps: list[Step] = []
    if not isinstance(raw_steps, list) or not raw_steps:
        c.fail('', "at least one [[steps]] entry is required")
    else:
        seen: set[str] = set()
        for i, raw in enumerate(raw_steps):
            step = _parse_step(c, raw, i, seen)
            if step is not None:
                steps.append(step)
    if c.problems:
        raise TaskError(path, c.problems)
    return Task(task_id, path, name, description, icon, keywords, mode, tuple(steps))


def load_task(path: Path) -> Task:
    path = Path(path)
    try:
        with path.open('rb') as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise TaskError(path, ['no such task file']) from None
    except tomllib.TOMLDecodeError as e:
        raise TaskError(path, [f'TOML syntax error: {e}']) from None
    return parse_task(data, path.stem, path)


def find_task(task_id: str) -> Task:
    if not is_valid_task_id(task_id):
        raise TaskError(Path(task_id), [f'invalid task ID {task_id!r} (must match {TASK_ID_RE.pattern})'])
    return load_task(task_path(task_id))


def load_tasks(directory: Path | None = None) -> tuple[list[Task], list[TaskError]]:
    directory = directory or paths.tasks_dir()
    tasks, errors = [], []
    for path in sorted(directory.glob('*.toml')):
        try:
            tasks.append(load_task(path))
        except TaskError as e:
            errors.append(e)
    return tasks, errors


TEMPLATE = '''\
# dwf task. Run `dwf check {id}` after editing; `dwf sync` updates the launcher entry.
version = 1
name = "{name}"
description = ""
# icon = "applications-internet"     # icon name from your theme, or an absolute path
# keywords = ["mail", "chat"]        # extra launcher search terms
mode = "parallel"                    # "parallel" or "serial" (serial stops at the first failure)

# An app, by desktop ID (the .desktop file name without the suffix).
[[steps]]
id = "mail"
app = "org.mozilla.thunderbird"

# A browser window with one tab per URL. `profile` is optional: a Firefox profile name or path, or a Chrome
# profile display name or directory ("Profile 1").
[[steps]]
id = "web"
browser = "org.mozilla.firefox"
urls = ["https://example.org"]
# profile = "default-release"

# A command, run to completion; a non-zero exit fails the step.
# [[steps]]
# id = "sync"
# command = ["rsync", "-a", "src/", "dst/"]
# timeout = 300
'''


def template(task_id: str) -> str:
    return TEMPLATE.format(id=task_id, name=task_id.replace('-', ' ').title())
