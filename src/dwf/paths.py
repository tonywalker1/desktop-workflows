"""XDG base directories used by dwf. Resolved on every call so tests and callers can change the environment."""

import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var, '')
    # The XDG spec says relative paths are invalid and must be ignored.
    return Path(value) if os.path.isabs(value) else Path.home() / default


def config_home() -> Path:
    return _xdg('XDG_CONFIG_HOME', '.config')


def data_home() -> Path:
    return _xdg('XDG_DATA_HOME', '.local/share')


def state_home() -> Path:
    return _xdg('XDG_STATE_HOME', '.local/state')


def data_dirs() -> list[Path]:
    """Data directories in lookup order: XDG_DATA_HOME first, then XDG_DATA_DIRS."""
    raw = os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share'
    return [data_home()] + [Path(p) for p in raw.split(':') if os.path.isabs(p)]


def tasks_dir() -> Path:
    return config_home() / 'dwf' / 'tasks'


def state_dir() -> Path:
    return state_home() / 'dwf'


def applications_dir() -> Path:
    return data_home() / 'applications'
