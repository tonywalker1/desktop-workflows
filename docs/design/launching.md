# Launching apps and browser windows

How `dwf` should start desktop apps and multi-tab browser windows, and why. Every claim below comes from the scripts
in [spikes/launching/](../../spikes/launching/); they are canonical, and this document records what they showed and
what it means for the design. Re-run them after browser or desktop upgrades and update this file when results change.
The runner implements these recipes in [src/dwf/browsers.py](../../src/dwf/browsers.py) and
[src/dwf/launch.py](../../src/dwf/launch.py).

| Script | Establishes |
|---|---|
| [firefox.sh](../../spikes/launching/firefox.sh) | Firefox window/tab routing, cold vs. warm, races (~2.5 min) |
| [chrome.sh](../../spikes/launching/chrome.sh) | Chrome window/tab routing and profiles (~40 s) |
| [scopes.sh](../../spikes/launching/scopes.sh) | Which cgroup a `.desktop`-launched native app lands in |

The scripts open and close real windows on the current desktop. They use throwaway profiles inside each Flatpak's
own `~/.var/app/<id>/` directory (the only host path the sandbox can see) and never touch the user's profiles.
`DWF_SPIKE_KEEP=1 spikes/launching/firefox.sh` keeps the work directory for debugging.

Last run: 2026-11-14, Debian 13, GNOME Shell 48.7 on Wayland, Flatpak Firefox 160.0, Flatpak Chrome 157.0.8103.78,
Flatpak Thunderbird 160.0.

## Terms

- **Cold**: no instance of that browser profile is running; the launcher process becomes the browser.
- **Warm**: an instance is running; the launcher hands the request to it over D-Bus and exits.
- **Ground truth**: Firefox's session file (`sessionstore-backups/recovery.jsonlz4`, decoded by
  [ffsession.py](../../spikes/launching/ffsession.py)); Chrome's AT-SPI tree
  ([a11y_windows.py](../../spikes/launching/a11y_windows.py)) with accessibility forced on for the test instance.

## Findings

### 1. Launcher exit status says almost nothing

- `gio launch` returns in ~8 ms (`launcher rc=0 after 8 ms` in `scopes.sh`): it spawns and forgets.
- A warm Firefox launcher returns in ~170 ms, a warm Chrome launcher in ~250 ms.
- A cold browser launcher does not return until the browser exits.
- A brand-new Firefox profile restarts itself on first use, so its launcher exits after ~1 s while the browser keeps
  running in a new process tree.
- A launch remoted to an instance that is shutting down is dropped silently and still returns 0 (see finding 5).

**Consequence**: a `spawn` step succeeds when the hand-off succeeds, and nothing more. Confirming that a window
appeared needs a different signal (see "Open questions").

### 2. Firefox: only some command forms put tabs where you expect

From `firefox.sh`; window 0 is the pre-existing window in the warm cases:

```text
== FF1 cold, one call: --new-window A1 A2 A3
  window[0] tabs=['A2', 'A3']
  window[1] tabs=['A1']

== FF2 warm, one call: --new-window B1 B2
  window[0] tabs=['about:blank', 'B2']
  window[1] tabs=['B1']

== FF3 warm, one call: --new-window C1 --new-tab C2
  window[0] tabs=['about:blank', 'C2']
  window[1] tabs=['C1']

== FF4 warm, sequential calls: --new-window D1; --new-tab D2; --new-tab D3
  warm launcher returned in 171 ms
  window[0] tabs=['about:blank']
  window[1] tabs=['D1', 'D2', 'D3']

== FF5 warm, sequential calls, no delay, 8 trials (same profile as FF4)
  8/8 trials kept all three tabs in the new window

== FF6 cold, plain URLs: E1 E2 E3 (no --new-window)
  ready after 546 ms (org.mozilla.firefox.L2hvbWUvdG9ueS8udmFy...)

== FF7 immediately after FF6 is ready: --new-window F1; --new-tab F2; --new-tab F3
  window[0] tabs=['E1', 'E2', 'E3', 'F2']
  window[1] tabs=['F1', 'F3']
```

`--new-window` takes exactly one URL. Any further URL, whether bare or given with `--new-tab` in the same call, goes
to whichever window Firefox considers most recent *before* the new one exists. Sequential calls, each waiting for the
previous launcher to exit, route correctly once the instance has settled (FF4, FF5), but not in the first moments
after a cold start (FF7).

**Recipe for one window with tabs U1..Un in profile P**:

- P not running: one call, `firefox --profile P U1 … Un` (or `-P <name>`), with no `--new-window` (FF6).
- P running: `--new-window U1`, then `--new-tab Ui` for each remaining URL, one call at a time.
- A task that opens a second window for a profile it just cold-started needs a settle delay before the sequential
  recipe. That delay is a heuristic, not a guarantee.

Tabs could still go astray if the user focuses a different window of the same profile in the middle of a sequence.
That window is ~170 ms per call, which is acceptable.

### 3. Firefox: profiles are separate instances with their own D-Bus name

Each profile registers `org.mozilla.firefox.<base64 of the profile path, '=' replaced by '_'>` on the session bus,
roughly 540 ms after a cold launch. The user's own Firefox and the test profiles ran side by side without
interference. Use the name's presence to choose between the cold and warm recipes. It does **not** mean the instance
is ready for the sequential recipe (FF7).

### 4. Firefox: two simultaneous cold launches of one profile are benign

```text
== FF8 race: two simultaneous cold launches of one stopped profile (G1, G2)
  browser main processes: 1
  window[0] tabs=['G2']
  window[1] tabs=['G1']
  log: [2] Wayland Proxy [0x7f4bced6bf20] Error: StartProxyServer(): bind() error : Address already in use
```

One sample per run, but consistent across every run so far. The loser's log error is harmless.

### 5. Firefox: launching while an instance is shutting down drops the request

The profile's remoting name outlives the main process by 35–121 ms (`remoting name lingered … ms` lines in
`firefox.sh`). In the first version of `firefox.sh`, a cold launch issued right after the main process exited
returned 0 and opened nothing. The likely cause is that the request was remoted to the dying instance. Waiting for
the name to disappear fixed it.

**Consequence**: if the user closes the browser and re-runs a task at once, a window can vanish without any error.
Before launching, `dwf` should check for a name whose owner is exiting, or wait briefly for the name to settle.

### 6. Chrome: one call does the right thing, cold or warm

From `chrome.sh`:

```text
== GC1 cold, one call: --new-window A1 A2
  tabs visible after 1150 ms
    window[0] title='DWF-A1 - Google Chrome' tabs=['DWF-A1', 'DWF-A2']

== GC2 warm, one call: --new-window B1 B2
  warm launcher returned in 251 ms
    window[1] title='DWF-B1 - Google Chrome' tabs=['DWF-B1', 'DWF-B2']

== GC3 warm, two profiles: --profile-directory="Profile 1" C1 C2; --profile-directory="Profile 2" D1 D2
    window[2] title='DWF-C1 - Google Chrome - Person 1' tabs=['DWF-C1', 'DWF-C2']
    window[3] title='DWF-D1 - Google Chrome - Person 2' tabs=['DWF-D1', 'DWF-D2']
  browser main processes: 1

== GC4 profile directory -> display name, from Local State
  while running: no profile.info_cache yet
  after exit:    {'Default': 'Your Chrome', 'Profile 1': 'Person 1', 'Profile 2': 'Person 2'}
```

**Recipe**: `chrome --profile-directory=<dir> --new-window U1 … Un`. All profiles of one `--user-data-dir` share a
single browser process.

Users will name profiles by display name ("Work"), but Chrome wants the directory (`Profile 1`). The mapping is in
`<user-data-dir>/Local State` under `profile.info_cache`. A freshly created data directory has no such section until
Chrome writes it, which happened on exit in this test. Map names when the task is validated, and report unknown names
as errors.

### 7. Browser steps must be built from the `.desktop` file's `Exec=`, not via `gio launch`

`gio launch FILE [URI…]` can only forward URIs, but the recipes above need flags. For Flatpaks the real command line
is inside `Exec=`:

```text
Exec=/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=firefox --file-forwarding org.mozilla.firefox @@u %u @@
Exec=/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=/app/bin/chrome --file-forwarding com.google.Chrome @@u %U @@
```

`dwf` should parse `Exec=` per the Desktop Entry spec, drop the field codes and Flatpak's `--file-forwarding` and
`@@u … @@` markers, then append the browser flags and URLs. File forwarding only matters for local files, and dwf
passes URLs. Resolve apps by desktop ID (`org.mozilla.firefox`), never by binary name: this machine has both
`firefox-esr` (deb) and `org.mozilla.firefox` (Flatpak).

### 8. Native apps inherit the launcher's cgroup; Flatpaks do not

From `scopes.sh`, run from a terminal inside VS Code:

```text
this script's cgroup: /user.slice/user-1000.slice/user@1000.service/app.slice/app-com.microsoft.VSCode-3943.scope

== S1 gio launch
  launcher rc=0 after 8 ms
  pid=33657 ppid=2604 (systemd)
  cgroup: /user.slice/user-1000.slice/user@1000.service/app.slice/app-com.microsoft.VSCode-3943.scope

== S2 systemd-run --user --scope ... gio launch
  launcher rc=0 after 39 ms
  pid=33736 ppid=2604 (systemd)
  cgroup: /user.slice/user-1000.slice/user@1000.service/app.slice/app-dwf-spike-33637.scope
```

Flatpak apps always get their own scope; Thunderbird launched by `gio launch` landed in
`app-flatpak-org.mozilla.thunderbird-2331995271.scope`.

**Consequence**: wrap every native launch in
`systemd-run --user --scope --collect --slice=app.slice --unit=app-dwf-<task>-<step>-<random>.scope`. Otherwise apps
share the runner's resource accounting and OOM fate, which means the scope of whoever started `dwf`. A per-step scope
also identifies which step owns a process, which a window-tracking backend can use. Without systemd, fall back to
`setsid`.

## Rejected approaches

- **AT-SPI as a window-readiness signal.** It works only when the app runs with accessibility enabled. With GNOME's
  `toolkit-accessibility` off (the default), ordinary app instances are absent from the bus, and even when present
  the Firefox tree omits tabs for some windows.
- **Browser remote-control protocols (CDP, Marionette, WebDriver BiDi).** They would make tab placement deterministic,
  but they open a control endpoint on a logged-in profile that any local process could drive.
- **`--new-window` with several URLs, or `--new-window … --new-tab …` in one Firefox call.** Tabs land in the wrong
  window (FF1–FF3).

## Open questions

- **Window readiness and placement** need compositor cooperation on Wayland: a GNOME Shell extension, or a KWin
  script on KDE. To be tested separately.
- **Not yet tested**: Firefox `-P <name>` against real named profiles, deb-packaged `firefox-esr`, Chromium and other
  Chromium-based browsers, KDE Plasma.
- **The FF7 settle delay** has not been measured.
