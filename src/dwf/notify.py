"""Logging setup and desktop notifications.

A task started from the launcher has no terminal, so failures must reach the user some other way: a desktop
notification, plus a log file under $XDG_STATE_HOME/dwf/ (stderr also lands in the journal on systemd desktops).
"""

from __future__ import annotations

import logging
import logging.handlers
import subprocess

from dwf import paths

log = logging.getLogger('dwf')

LOG_FILE_BYTES = 512 * 1024
LOG_FILE_BACKUPS = 3


def setup_logging(verbose: bool = False, to_file: bool = True) -> None:
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    stderr = logging.StreamHandler()
    stderr.setLevel(logging.DEBUG if verbose else logging.WARNING)
    stderr.setFormatter(logging.Formatter('dwf: %(message)s'))
    log.addHandler(stderr)
    if to_file:
        try:
            paths.state_dir().mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                paths.state_dir() / 'dwf.log', maxBytes=LOG_FILE_BYTES, backupCount=LOG_FILE_BACKUPS,
                encoding='utf-8')
        except OSError as e:
            log.warning('cannot open log file: %s', e)
        else:
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-7s %(threadName)s %(message)s'))
            log.addHandler(handler)


def _gvariant_str(s: str) -> str:
    return "'" + s.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n') + "'"


def notify_argv(summary: str, body: str, icon: str, critical: bool) -> list[str]:
    hints = '{"urgency": <byte 2>}' if critical else '{"urgency": <byte 1>}'
    # Arguments are GVariant text. The expiry timeout is spelled `int32 -1` because gdbus would parse a bare `-1`
    # as an option and print its usage instead of calling the method.
    return ['gdbus', 'call', '--session', '--dest', 'org.freedesktop.Notifications',
            '--object-path', '/org/freedesktop/Notifications', '--method', 'org.freedesktop.Notifications.Notify',
            "'dwf'", 'uint32 0', _gvariant_str(icon), _gvariant_str(summary), _gvariant_str(body), '@as []', hints,
            'int32 -1']


def notify(summary: str, body: str, icon: str = 'dialog-error', critical: bool = True) -> bool:
    """Send a desktop notification over org.freedesktop.Notifications (GNOME, KDE and most others)."""
    argv = notify_argv(summary, body, icon, critical)
    try:
        subprocess.run(argv, capture_output=True, timeout=5, check=True)
        return True
    except (OSError, subprocess.SubprocessError) as e:
        log.debug('notification failed: %s', e)
        return False
