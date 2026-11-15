"""Browser windows with several tabs, per browser family. The recipes come from docs/design/launching.md.

Firefox: profiles are separate instances, each registering org.mozilla.<app>.<base64(profile path)> on the session
bus. Cold (name absent): one call with all URLs gives one window. Warm: `--new-window U1`, then one `--new-tab Ui`
call per URL, each waiting for the previous launcher to exit; single-call forms put tabs in the wrong window.

Chromium family: `--profile-directory=<dir> --new-window U1 .. Un` is correct cold or warm.
"""

from __future__ import annotations

import base64
import configparser
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dwf import paths
from dwf.desktop import DesktopApp
from dwf.errors import StepFailed

log = logging.getLogger('dwf')

FIREFOX_MARKERS = ('firefox', 'librewolf')
CHROMIUM_MARKERS = ('chrome', 'chromium', 'brave', 'vivaldi', 'msedge', 'microsoft-edge')

# Firefox may still be registering its first window after its remoting name appears (FF7); calls for the same
# instance within this many seconds of a cold start wait out the remainder. Heuristic; see the design doc.
FIREFOX_SETTLE_S = 3.0
FIREFOX_COLD_START_TIMEOUT_S = 20.0
FIREFOX_WARM_CALL_TIMEOUT_S = 15.0
# A warm Chromium launcher exits in ~250 ms; one still running after this is the new browser instance.
CHROMIUM_HANDOFF_WAIT_S = 3.0


def family_of(app: DesktopApp, explicit: str | None) -> str:
    if explicit:
        return explicit
    haystack = ' '.join([app.id, *app.exec_argv()[:8]]).lower()
    if any(m in haystack for m in FIREFOX_MARKERS):
        return 'firefox'
    if any(m in haystack for m in CHROMIUM_MARKERS):
        return 'chromium'
    raise StepFailed(f'cannot tell which browser family {app.id} belongs to; set family = "firefox" or "chromium"')


# --- Firefox ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FirefoxProfile:
    path: str | None      # as the browser sees it (inside the sandbox for Flatpaks); None if unknown
    explicit: bool        # chosen by the task, so it must be passed with --profile


def _read_ini(path: Path) -> configparser.ConfigParser:
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    ini.optionxform = str
    ini.read(path, encoding='utf-8')
    return ini


def firefox_roots(app: DesktopApp) -> list[tuple[Path, Path]]:
    """Candidate (host path, path inside the app) pairs for the directory holding profiles.ini, in Firefox's own
    preference order: the legacy ~/.mozilla location wins over the XDG one when both exist."""
    home = Path.home()
    if app.flatpak_id:
        base = home / '.var' / 'app' / app.flatpak_id
        # Flatpak Firefox persists ~/.mozilla to ~/.var/app/<id>/.mozilla; XDG_CONFIG_HOME is <base>/config.
        return [(base / '.mozilla' / 'firefox', home / '.mozilla' / 'firefox'),
                (base / 'config' / 'mozilla' / 'firefox',) * 2]
    xdg = paths.config_home() / 'mozilla' / 'firefox'
    return [(home / '.mozilla' / 'firefox',) * 2, (xdg, xdg)]


def _profile_root(app: DesktopApp) -> tuple[Path, Path] | None:
    for host, inside in firefox_roots(app):
        if (host / 'profiles.ini').is_file():
            return host, inside
    return None


def _profile_path(section: configparser.SectionProxy, inside: Path) -> str:
    rel = section.get('Path', '')
    return str(inside / rel) if section.get('IsRelative', '1') == '1' else rel


def resolve_firefox_profile(app: DesktopApp, name: str | None) -> FirefoxProfile:
    if name and (name.startswith('/') or name.startswith('~')):
        return FirefoxProfile(os.path.expanduser(name), explicit=True)
    root = _profile_root(app)
    if root is None:
        if name:
            raise StepFailed(f'no Firefox profiles.ini found for {app.id}; cannot resolve profile {name!r}')
        return FirefoxProfile(None, explicit=False)
    host, inside = root
    ini = _read_ini(host / 'profiles.ini')
    profiles = [ini[s] for s in ini.sections() if s.startswith('Profile') and ini[s].get('Path')]
    if name:
        for p in profiles:
            if name.casefold() in (p.get('Name', '').casefold(), Path(p['Path']).name.casefold()):
                return FirefoxProfile(_profile_path(p, inside), explicit=True)
        known = ', '.join(sorted(p.get('Name', p['Path']) for p in profiles)) or 'none'
        raise StepFailed(f'Firefox profile {name!r} not found in {host / "profiles.ini"} (known: {known})')
    # Default profile: the per-install default (installs.ini, or [Install*] in profiles.ini) when all installs
    # agree, else the profile marked Default=1.
    defaults = set()
    installs = host / 'installs.ini'
    for ini_file, prefix in ((installs, ''), (host / 'profiles.ini', 'Install')):
        if ini_file.is_file():
            parsed = _read_ini(ini_file)
            defaults |= {parsed[s]['Default'] for s in parsed.sections()
                         if s.startswith(prefix) and parsed[s].get('Default')}
    if len(defaults) == 1:
        rel = defaults.pop()
        return FirefoxProfile(rel if rel.startswith('/') else str(inside / rel), explicit=False)
    for p in profiles:
        if p.get('Default') == '1':
            return FirefoxProfile(_profile_path(p, inside), explicit=False)
    return FirefoxProfile(None, explicit=False)


def _b64_norm(s: str) -> str:
    # Firefox maps base64's '=' to '_' to form a valid bus name; normalize '+', '/', '-' too so we need not know
    # its exact mapping for those.
    return re.sub(r'[+/=_-]', '_', s)


def remote_name_matches(bus_name: str, profile_path: str) -> bool:
    if not bus_name.startswith('org.mozilla.'):
        return False
    encoded = base64.b64encode(profile_path.encode()).decode()
    return _b64_norm(bus_name.rsplit('.', 1)[-1]) == _b64_norm(encoded)


def firefox_running(system, profile_path: str | None) -> bool | None:
    if profile_path is None:
        return None
    names = system.bus_names()
    if names is None:
        return None
    return any(remote_name_matches(n, profile_path) for n in names)


def launch_firefox(step, app: DesktopApp, ctx) -> str:
    profile = resolve_firefox_profile(app, step.profile)
    base = app.launch_argv() + (['--profile', profile.path] if profile.explicit else [])
    key = ('firefox', app.id, profile.path or '<default>')
    with ctx.lock(key):
        running = firefox_running(ctx.system, profile.path)
        if running is False:
            proc = ctx.system.spawn(base + list(step.urls), unit=ctx.unit(step.id), label=step.id)

            def up_or_failed() -> bool:
                return firefox_running(ctx.system, profile.path) or proc.poll() not in (None, 0)

            # A new profile restarts itself once (launcher exits 0, browser lives on), so keep polling on rc 0.
            if not ctx.system.wait_until(up_or_failed, FIREFOX_COLD_START_TIMEOUT_S):
                raise StepFailed(f'Firefox did not start within {FIREFOX_COLD_START_TIMEOUT_S:g} s')
            rc = proc.poll()
            if rc not in (None, 0) and not firefox_running(ctx.system, profile.path):
                raise StepFailed(f'Firefox exited with status {rc}')
            ctx.mark_cold_start(key)
            return f'started Firefox with {len(step.urls)} tab(s)'

        if running is None:
            log.warning('%s: cannot tell whether the Firefox profile is running; assuming it is', step.id)
        ctx.settle(key, FIREFOX_SETTLE_S)
        for i, url in enumerate(step.urls):
            flag = '--new-window' if i == 0 else '--new-tab'
            proc = ctx.system.spawn(base + [flag, url], unit=ctx.unit(step.id), label=step.id)
            rc = proc.wait(FIREFOX_WARM_CALL_TIMEOUT_S)
            if rc is None:
                # The launcher became the browser: the instance was not (or no longer) running. Let it settle
                # before routing the remaining tabs to it.
                log.warning('%s: Firefox launcher did not hand off; it started a new instance', step.id)
                ctx.mark_cold_start(key)
                ctx.settle(key, FIREFOX_SETTLE_S)
            elif rc != 0:
                raise StepFailed(f'{flag} {url}: Firefox launcher exited with status {rc}')
        return f'opened a window with {len(step.urls)} tab(s) in running Firefox'


# --- Chromium family -------------------------------------------------------------------------------------------------

def _chromium_user_data_dirs() -> dict[str, Path]:
    home, cfg = Path.home(), paths.config_home()

    def fp(app_id: str, rel: str) -> Path:
        return home / '.var' / 'app' / app_id / 'config' / rel

    return {
        'com.google.Chrome': fp('com.google.Chrome', 'google-chrome'),
        'google-chrome': cfg / 'google-chrome',
        'google-chrome-beta': cfg / 'google-chrome-beta',
        'org.chromium.Chromium': fp('org.chromium.Chromium', 'chromium'),
        'chromium': cfg / 'chromium',
        'com.brave.Browser': fp('com.brave.Browser', 'BraveSoftware/Brave-Browser'),
        'brave-browser': cfg / 'BraveSoftware' / 'Brave-Browser',
        'com.microsoft.Edge': fp('com.microsoft.Edge', 'microsoft-edge'),
        'microsoft-edge': cfg / 'microsoft-edge',
        'com.vivaldi.Vivaldi': fp('com.vivaldi.Vivaldi', 'vivaldi'),
        'vivaldi-stable': cfg / 'vivaldi',
    }


def chromium_user_data_dir(app: DesktopApp, explicit: str | None) -> Path | None:
    if explicit:
        return Path(os.path.expanduser(explicit))
    return _chromium_user_data_dirs().get(app.id)


def resolve_chromium_profile(user_data_dir: Path | None, name: str, app_id: str) -> str:
    """Map a profile display name, account name or directory name to the profile directory."""
    if user_data_dir is None:
        raise StepFailed(f'do not know where {app_id} keeps its profiles; set user_data_dir to resolve {name!r}')
    state = user_data_dir / 'Local State'
    try:
        cache = json.loads(state.read_text(encoding='utf-8')).get('profile', {}).get('info_cache', {})
    except FileNotFoundError:
        cache = {}
    except (OSError, ValueError) as e:
        raise StepFailed(f'cannot read {state}: {e}') from None
    want = name.casefold()
    for directory, info in cache.items():
        labels = {directory, info.get('name', ''), info.get('user_name', ''), info.get('gaia_name', '')}
        if want in {label.casefold() for label in labels if label}:
            return directory
    if (user_data_dir / name).is_dir():
        return name
    known = ', '.join(f'{info.get("name", d)} ({d})' for d, info in sorted(cache.items())) or 'none yet'
    raise StepFailed(f'Chrome profile {name!r} not found in {state} (known: {known})')


def launch_chromium(step, app: DesktopApp, ctx) -> str:
    udd = chromium_user_data_dir(app, step.user_data_dir)
    args = app.launch_argv()
    if step.user_data_dir:
        args.append(f'--user-data-dir={udd}')
    if step.profile:
        args.append(f'--profile-directory={resolve_chromium_profile(udd, step.profile, app.id)}')
    args += ['--new-window', *step.urls]
    with ctx.lock(('chromium', app.id, str(udd))):
        proc = ctx.system.spawn(args, unit=ctx.unit(step.id), label=step.id)
        rc = proc.wait(CHROMIUM_HANDOFF_WAIT_S)
    if rc is None:
        return f'started {app.name} with {len(step.urls)} tab(s)'
    if rc != 0:
        raise StepFailed(f'{app.name} launcher exited with status {rc}')
    return f'opened a window with {len(step.urls)} tab(s) in running {app.name}'
