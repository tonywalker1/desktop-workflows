"""Desktop Entry files: looking up apps, parsing Exec lines, and generating dwf's own launcher entries.

Implements the parts of the freedesktop.org Desktop Entry Specification (1.5) that dwf needs: the string escape
rules, the Exec quoting rules (applied after string unescaping, hence four backslashes for a literal one inside a
quoted argument), field codes, and lookup by desktop ID across XDG data directories.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dwf import paths

FIELD_CODES = frozenset({'%f', '%F', '%u', '%U', '%i', '%c', '%k', '%d', '%D', '%n', '%N', '%v', '%m'})
FLATPAK_FORWARDING = frozenset({'--file-forwarding', '@@', '@@u'})
EXEC_RESERVED = frozenset(' \t\n"\'\\><~|&;$*?#()`')

ENTRY_PREFIX = 'dwf-'
MANAGED_KEY = 'X-Dwf-Managed'
TASK_KEY = 'X-Dwf-Task'
DEFAULT_ICON = 'system-run'


class DesktopEntryError(Exception):
    pass


# --- string and Exec syntax ------------------------------------------------------------------------------------------

_UNESCAPES = {'s': ' ', 'n': '\n', 't': '\t', 'r': '\r', '\\': '\\'}


def unescape_value(value: str) -> str:
    out = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == '\\' and i + 1 < len(value):
            nxt = value[i + 1]
            out.append(_UNESCAPES.get(nxt, '\\' + nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def escape_value(value: str) -> str:
    v = value.replace('\\', '\\\\').replace('\n', '\\n').replace('\t', '\\t').replace('\r', '\\r')
    return '\\s' + v[1:] if v.startswith(' ') else v


def split_exec(value: str) -> list[str]:
    """Split a string-unescaped Exec value into argv using the spec's double-quote rules."""
    args: list[str] = []
    cur: list[str] = []
    in_arg = False
    i, n = 0, len(value)
    while i < n:
        c = value[i]
        if c == '"':
            in_arg = True
            i += 1
            while True:
                if i >= n:
                    raise DesktopEntryError(f'unterminated quote in Exec: {value!r}')
                c = value[i]
                if c == '\\' and i + 1 < n and value[i + 1] in '"`$\\':
                    cur.append(value[i + 1])
                    i += 2
                elif c == '"':
                    i += 1
                    break
                else:
                    cur.append(c)
                    i += 1
        elif c in ' \t\n':
            if in_arg:
                args.append(''.join(cur))
                cur, in_arg = [], False
            i += 1
        else:
            cur.append(c)
            in_arg = True
            i += 1
    if in_arg:
        args.append(''.join(cur))
    return args


def strip_field_codes(argv: list[str]) -> list[str]:
    """Drop standalone field codes, remove embedded ones, and turn %% into %."""
    out = []
    for arg in argv:
        if arg in FIELD_CODES:
            continue
        out.append(re.sub(r'%(.)', lambda m: '%' if m.group(1) == '%' else '', arg))
    return out


def quote_exec_arg(arg: str) -> str:
    if arg and not EXEC_RESERVED.intersection(arg):
        return arg.replace('%', '%%')
    return '"' + re.sub(r'(["`$\\])', r'\\\1', arg).replace('%', '%%') + '"'


def join_exec(argv: list[str]) -> str:
    """Build an Exec value (already string-escaped) that split_exec(unescape_value(...)) maps back to argv."""
    return escape_value(' '.join(quote_exec_arg(a) for a in argv))


# --- reading entries and finding apps --------------------------------------------------------------------------------

def read_entry(path: Path) -> dict[str, str]:
    """Return the raw (still escaped) keys of the [Desktop Entry] group."""
    group = None
    entry: dict[str, str] = {}
    for raw in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('[') and line.endswith(']'):
            group = line[1:-1]
            continue
        if group == 'Desktop Entry' and '=' in line:
            key, value = line.split('=', 1)
            entry.setdefault(key.strip(), value.lstrip())
    return entry


def normalize_id(app_id: str) -> str:
    return app_id.removesuffix('.desktop')


@dataclass(frozen=True)
class DesktopApp:
    id: str
    path: Path
    entry: dict[str, str]

    @property
    def name(self) -> str:
        return unescape_value(self.entry.get('Name', self.id))

    @property
    def flatpak_id(self) -> str | None:
        return self.entry.get('X-Flatpak') or None

    def exec_argv(self) -> list[str]:
        value = self.entry.get('Exec')
        if not value:
            raise DesktopEntryError(f'{self.path}: no Exec key')
        return split_exec(unescape_value(value))

    def launch_argv(self) -> list[str]:
        """Exec argv with field codes and Flatpak file-forwarding markers removed, ready for extra arguments."""
        return [a for a in strip_field_codes(self.exec_argv()) if a not in FLATPAK_FORWARDING]


def find_app(app_id: str, data_dirs: list[Path] | None = None) -> DesktopApp | None:
    """Find a desktop entry by ID. The first match wins; a Hidden=true entry masks lower-priority ones."""
    app_id = normalize_id(app_id)
    for d in data_dirs if data_dirs is not None else paths.data_dirs():
        path = d / 'applications' / f'{app_id}.desktop'
        if path.is_file():
            entry = read_entry(path)
            if entry.get('Hidden', '').lower() == 'true':
                return None
            return DesktopApp(app_id, path, entry)
    return None


# --- generating dwf's own entries ------------------------------------------------------------------------------------

def entry_path(task_id: str, apps_dir: Path | None = None) -> Path:
    return (apps_dir or paths.applications_dir()) / f'{ENTRY_PREFIX}{task_id}.desktop'


def render_entry(task, exec_path: str) -> str:
    """Render the launcher entry for a task (dwf.task.Task)."""
    keywords = ['dwf', 'workflow', *task.keywords]
    lines = [
        f'# Generated by dwf from {task.path}. Edit the task, then run `dwf sync`; changes here are overwritten.',
        '[Desktop Entry]',
        'Type=Application',
        'Version=1.5',
        f'Name={escape_value(task.name)}',
    ]
    if task.description:
        lines.append(f'Comment={escape_value(task.description)}')
    lines += [
        f'Icon={escape_value(task.icon or DEFAULT_ICON)}',
        'Exec=' + join_exec([exec_path, 'run', task.id]),
        'Terminal=false',
        # No window maps for the runner itself; startup notification would leave a spinner until it times out.
        'StartupNotify=false',
        'Categories=Utility;',
        'Keywords=' + ''.join(escape_value(k).replace(';', '\\;') + ';' for k in keywords),
        f'{MANAGED_KEY}=true',
        f'{TASK_KEY}={task.id}',
    ]
    return '\n'.join(lines) + '\n'


def managed_entries(apps_dir: Path | None = None) -> dict[str, Path]:
    """Map task ID -> entry file for every launcher entry dwf generated."""
    apps_dir = apps_dir or paths.applications_dir()
    found = {}
    for path in sorted(apps_dir.glob(f'{ENTRY_PREFIX}*.desktop')):
        entry = read_entry(path)
        if entry.get(MANAGED_KEY) == 'true':
            found[entry.get(TASK_KEY) or path.stem.removeprefix(ENTRY_PREFIX)] = path
    return found


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


@dataclass
class SyncResult:
    written: list[Path]
    unchanged: list[Path]
    removed: list[Path]


def sync_entries(tasks, exec_path: str, keep: set[str] = frozenset(), apps_dir: Path | None = None) -> SyncResult:
    """Write an entry per task and remove managed entries for tasks that no longer exist.

    `keep` lists task IDs whose files exist but failed to load; their current entries are left alone.
    """
    apps_dir = apps_dir or paths.applications_dir()
    result = SyncResult([], [], [])
    wanted = set()
    for task in tasks:
        path = entry_path(task.id, apps_dir)
        wanted.add(task.id)
        text = render_entry(task, exec_path)
        if path.is_file() and path.read_text(encoding='utf-8') == text:
            result.unchanged.append(path)
        else:
            write_atomic(path, text)
            result.written.append(path)
    for task_id, path in managed_entries(apps_dir).items():
        if task_id not in wanted and task_id not in keep:
            path.unlink()
            result.removed.append(path)
    return result


def remove_all_entries(apps_dir: Path | None = None) -> list[Path]:
    removed = []
    for path in managed_entries(apps_dir).values():
        path.unlink()
        removed.append(path)
    return removed
