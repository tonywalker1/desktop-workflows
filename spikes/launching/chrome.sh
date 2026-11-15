#!/usr/bin/env bash
# Google Chrome (Flatpak) window/tab/profile experiments.
#
# Uses a throwaway --user-data-dir under the sandbox's own ~/.var/app directory, so it runs as a separate instance
# and never touches the user's Chrome data. Windows and tabs are read over AT-SPI (a11y_windows.py), which works
# for Chrome only because this instance is started with accessibility forced on.
#
# Usage: spikes/launching/chrome.sh     (runtime ~40 s)

source "$(dirname "$0")/lib.sh"

APP=com.google.Chrome
require_flatpak "$APP"
WORK=$HOME/.var/app/$APP/dwf-spike.$$
PAGES=$WORK/pages
UDD=$WORK/udd
GC=(flatpak run --branch=stable --arch=x86_64 --command=/app/bin/chrome --env=ACCESSIBILITY_ENABLED=1 "$APP"
    --no-first-run --no-default-browser-check --force-renderer-accessibility --user-data-dir="$UDD")

# The browser process is the one without --type= (renderers, GPU, zygotes and utilities all carry --type=).
main_pids() {
    pgrep -af "^/app/extra/chrome .*--user-data-dir=$UDD( |$)" | grep -v -- ' --type=' | cut -d' ' -f1 || true
}

stop() {
    local pids
    pids=$(main_pids)
    [ -z "$pids" ] && return 0
    kill $pids 2>/dev/null || true
    for _ in $(seq 1 50); do [ -z "$(main_pids)" ] && return 0; sleep 0.2; done
    kill -KILL $pids 2>/dev/null || true
}

cleanup() { stop; rm -rf -- "${WORK:?}"; }
trap cleanup EXIT

url() { echo "file://$PAGES/$1.html"; }
gc() { "${GC[@]}" "$@" >>"$WORK/chrome.log" 2>&1 || true; }
windows() { python3 "$SPIKE_DIR/a11y_windows.py" chrome 2>/dev/null; }

# wait_for <page-name>: poll AT-SPI until a tab titled DWF-<page-name> exists
wait_for() {
    for _ in $(seq 1 100); do windows | grep -q "DWF-$1'" && return 0; sleep 0.2; done
    echo "  timed out waiting for $1"
}

profiles() {
    python3 -c 'import json, sys; d = json.load(open(sys.argv[1]))
cache = d.get("profile", {}).get("info_cache")
print({k: v.get("name") for k, v in cache.items()} if cache else "no profile.info_cache yet")' "$UDD/Local State"
}

mkdir -p "$UDD"
make_pages "$PAGES" A1 A2 B1 B2 C1 C2 D1 D2
echo "Chrome $(flatpak info "$APP" | awk '/Version:/ {print $2}'), work dir $WORK"

header 'GC1 cold, one call: --new-window A1 A2'
echo 'expect: one window with A1 A2'
t0=$(now_ms)
( gc --new-window "$(url A1)" "$(url A2)" ) </dev/null &
wait_for A2; echo "  tabs visible after $(( $(now_ms) - t0 )) ms"
windows

header 'GC2 warm, one call: --new-window B1 B2'
echo 'expect: new window with B1 B2'
t0=$(now_ms); gc --new-window "$(url B1)" "$(url B2)"; echo "  warm launcher returned in $(( $(now_ms) - t0 )) ms"
wait_for B2; windows

header 'GC3 warm, two profiles: --profile-directory="Profile 1" C1 C2; --profile-directory="Profile 2" D1 D2'
echo 'expect: two more windows, one per profile, same browser process'
gc --profile-directory="Profile 1" --new-window "$(url C1)" "$(url C2)"
gc --profile-directory="Profile 2" --new-window "$(url D1)" "$(url D2)"
wait_for D2; windows
echo "  browser main processes: $(main_pids | wc -l)"

header 'GC4 profile directory -> display name, from Local State'
echo "  while running: $(profiles)"
stop
echo "  after exit:    $(profiles)"
