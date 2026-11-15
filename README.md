# desktop-workflows

`dwf` starts a *task* (a set of desktop apps, multi-tab browser windows and commands) from the desktop launcher
or the command line. Each task is a small TOML file; `dwf` generates a launcher entry for it, so pressing Super and
typing its name starts everything at once.

Status: early. It works on GNOME (Wayland) and should work on any freedesktop.org desktop with systemd. Window
placement is planned for GNOME via a Shell extension.

## Install

Needs Python 3.11 or newer and nothing else; `gio` (GLib) and `systemd-run` are used when present.

```sh
scripts/install.sh               # builds ~/.local/bin/dwf (a single-file zipapp) and runs `dwf sync`
scripts/install.sh --uninstall   # removes the executable and generated launcher entries; keeps your tasks
```

## Quick start

```sh
dwf edit comms      # creates ~/.config/dwf/tasks/comms.toml from a template, opens $EDITOR, then syncs launchers
dwf check comms     # validates the file and checks that its apps, browsers and profiles exist on this machine
dwf run comms       # or press Super and type the task's name
```

`dwf run` prints one line per step; this is a real run of a four-step task, first with nothing running, then again:

```text
ok      calc    launched Calculator (0.1 s)
ok      ff      started Firefox with 3 tab(s) (1.2 s)
ok      chrome  started Google Chrome with 2 tab(s) (3.0 s)
ok      cmd     completed (0.0 s)

ok      calc    launched Calculator (0.1 s)
ok      ff      opened a window with 3 tab(s) in running Firefox (0.8 s)
ok      chrome  opened a window with 2 tab(s) in running Google Chrome (0.4 s)
ok      cmd     completed (0.0 s)
```

From the launcher there is no terminal, so failures arrive as a desktop notification. Every run is logged to
`~/.local/state/dwf/dwf.log`.

## Commands

| Command | What it does |
|---|---|
| `dwf run TASK [-n] [--notify auto\|always\|never]` | Run a task. `-n`/`--dry-run` shows the commands only |
| `dwf list` | List tasks, whether each has a launcher entry, and any invalid files |
| `dwf check [TASK...]` | Validate tasks and resolve their apps and profiles without launching anything |
| `dwf edit TASK` | Create or edit a task in `$VISUAL`/`$EDITOR`, re-prompting until it is valid, then sync |
| `dwf sync [--exec PATH] [--remove-all]` | Write launcher entries for all tasks and remove entries for deleted tasks |

Exit status: 0 when every step succeeded, 1 when any step failed or was skipped, 2 for usage or task-file errors.

## Task files

Tasks live in `~/.config/dwf/tasks/<id>.toml`. The file name is the task ID: lowercase letters, digits and dashes.
See [examples/comms.toml](examples/comms.toml) for a complete example.

```toml
version = 1                      # required; the schema version
name = "Comms"                   # launcher name (default: the task ID)
description = "Mail and chat"    # launcher tooltip
icon = "mail-send"               # icon name or absolute path
keywords = ["mail", "chat"]      # extra launcher search terms
mode = "parallel"                # "parallel" (default) or "serial"

[[steps]]
id = "mail"                      # optional; defaults to <kind><position>, e.g. "app1"
app = "org.mozilla.thunderbird"  # desktop ID: the .desktop file name without the suffix

[[steps]]
id = "messages"
browser = "org.mozilla.firefox"  # desktop ID of a Firefox- or Chromium-family browser
urls = ["https://messages.google.com/web", "https://www.reddit.com"]   # one window, one tab per URL
profile = "work"                 # optional (see below)
# family = "chromium"            # only if dwf cannot tell from the desktop entry
# user_data_dir = "~/..."        # Chromium family: only for browsers dwf does not know

[[steps]]
id = "backup"
command = ["rsync", "-a", "src/", "dst/"]   # argv, no shell; use ["sh", "-c", "..."] if you need one
cwd = "~/work"                   # optional
timeout = 300                    # optional, seconds
```

Each step has exactly one of `app`, `browser` or `command`.

- **app**: started the way the desktop shell starts it (`gio launch`), each in its own systemd scope. The step
  succeeds once the app has been handed off, not when its window appears.
- **browser**: opens one new window with all `urls` as tabs. `profile` is optional:
  - Firefox: a profile name from `about:profiles`, the profile directory name, or an absolute path.
  - Chromium family: the name in the profile menu, the account email, or the directory (`Default`, `Profile 1`).
- **command**: runs to completion; a non-zero exit fails the step. `DWF_TASK` and `DWF_STEP` are set in its
  environment, and its output goes to the log.

In `parallel` mode all steps start together and a failure does not stop the others. In `serial` mode each step waits
for the previous one, and a failure skips the rest.

## How it works

Launching multi-tab windows reliably needs browser-specific recipes. For example, Firefox's
`--new-window URL1 URL2` puts URL2 in a *different* window. The recipes, the experiments behind them and the
rejected alternatives are in [docs/design/launching.md](docs/design/launching.md).

## Development

```sh
scripts/test.sh                                  # unit tests (stdlib unittest)
PYTHONPATH=src python3 -m dwf run --dry-run comms
spikes/launching/firefox.sh                      # re-run the launch experiments after browser upgrades
```

## License

MIT; see [LICENSE](LICENSE).
