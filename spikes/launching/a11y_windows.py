#!/usr/bin/env python3
"""Print top-level windows and tab titles for AT-SPI applications whose name contains <substring>.

Only useful when the target app was started with accessibility enabled (Chrome: ACCESSIBILITY_ENABLED=1 and
--force-renderer-accessibility). With GNOME's toolkit-accessibility off, ordinary app instances are absent from the
bus, which is why this cannot serve as a window-readiness signal for dwf.

Usage: a11y_windows.py <substring>
"""
import re
import sys

import gi

gi.require_version('Atspi', '2.0')
from gi.repository import Atspi  # noqa: E402

MAX_DEPTH = 60
MAX_CHILDREN = 500


def page_tabs(node, depth, out):
    if node is None or depth > MAX_DEPTH:
        return
    try:
        role, name, count = node.get_role(), node.get_name() or '', node.get_child_count()
    except Exception:
        return
    if role == Atspi.Role.PAGE_TAB:
        out.append(re.sub(r' - Memory usage - [\d.]+ [KMG]B$', '', name))
        return
    for i in range(min(count, MAX_CHILDREN)):
        try:
            page_tabs(node.get_child_at_index(i), depth + 1, out)
        except Exception:
            pass


def main() -> None:
    want = sys.argv[1].lower()
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if app is None or want not in (app.get_name() or '').lower():
            continue
        print(f'  app {app.get_name()!r}')
        for j in range(app.get_child_count()):
            frame = app.get_child_at_index(j)
            tabs = []
            page_tabs(frame, 0, tabs)
            print(f'    window[{j}] title={frame.get_name()!r} tabs={tabs}')


if __name__ == '__main__':
    main()
