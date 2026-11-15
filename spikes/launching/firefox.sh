#!/usr/bin/env bash
# Firefox (Flatpak) window/tab routing experiments.
#
# Opens and closes Firefox windows on the current desktop using throwaway profiles under the sandbox's own
# ~/.var/app directory (the only host path the Flatpak can see). The user's profiles are never touched: each
# --profile is a separate instance with its own D-Bus remoting name.
#
# Ground truth comes from each profile's session file (ffsession.py), written every second via user.js.
#
# Usage: spikes/launching/firefox.sh [warm-trials]     (default 8; runtime ~2.5 min)

source "$(dirname "$0")/lib.sh"

APP=org.mozilla.firefox
require_flatpak "$APP"
TRIALS=${1:-8}
WORK=$HOME/.var/app/$APP/dwf-spike.$$
PAGES=$WORK/pages
FF=(flatpak run --branch=stable --arch=x86_64 --command=firefox "$APP")

main_pids() { pgrep -f "^/app/lib/firefox/firefox .*--profile $WORK/$1( |$)" || true; }

mozilla_names() {
    busctl --user list --no-pager --no-legend 2>/dev/null | awk '$1 ~ /^org\.mozilla\.firefox\./ {print $1}' | sort
}
BASE_NAMES=$(mozilla_names)    # the user's own instances; experiments run one profile at a time

# stop <profile>: terminate the instance and wait until its remoting name is gone too. The name outlives the main
# process briefly; a launch issued in that window is remoted to the dying instance and silently dropped (rc=0).
# Prints how long the name lingered.
stop() {
    local pids t0
    pids=$(main_pids "$1")
    [ -z "$pids" ] && return 0
    kill $pids 2>/dev/null || true
    for _ in $(seq 1 50); do [ -z "$(main_pids "$1")" ] && break; sleep 0.2; done
    [ -n "$(main_pids "$1")" ] && kill -KILL $pids 2>/dev/null
    t0=$(now_ms)
    for _ in $(seq 1 100); do [ "$(mozilla_names)" = "$BASE_NAMES" ] && break; sleep 0.05; done
    echo "  (stopped $1; remoting name lingered $(( $(now_ms) - t0 )) ms after process exit)"
}

cleanup() {
    local pids
    pids=$(pgrep -f "^/app/lib/firefox/firefox .*--profile $WORK/" || true)
    [ -n "$pids" ] && kill $pids 2>/dev/null
    sleep 2
    if [ -n "${DWF_SPIKE_KEEP:-}" ]; then echo "kept $WORK"; else rm -rf -- "${WORK:?}"; fi
}
trap cleanup EXIT

url() { echo "file://$PAGES/$1.html"; }

# ff <profile> args...: run the launcher in the foreground (returns when it hands off, or when the browser exits
# if this call IS the browser)
ff() { local p=$1; shift; "${FF[@]}" --profile "$WORK/$p" "$@" >>"$WORK/$p.log" 2>&1 || true; }

# ff_bg <profile> args...: fire and forget (stdout detached so callers may use it inside $(...))
ff_bg() { ( ff "$@" ) </dev/null >/dev/null 2>&1 & }

# cold <profile> args...: start a stopped profile in the background; print ms until its D-Bus name appears
cold() {
    local before t0 name
    before=$(mozilla_names)
    t0=$(now_ms)
    ff_bg "$@"
    for _ in $(seq 1 300); do
        name=$(comm -13 <(echo "$before") <(mozilla_names) | head -1)
        [ -n "$name" ] && { echo "$(( $(now_ms) - t0 )) ms (${name:0:40}...)"; return 0; }
        sleep 0.05
    done
    echo 'D-Bus name never appeared'
}

# fresh <profile>: create a profile and run it once to absorb Firefox's first-run self-restart; leave it stopped
fresh() {
    local dir=$WORK/$1
    mkdir -p "$dir"
    cat > "$dir/user.js" <<'JS'
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.startup.page", 0);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.sessionstore.max_resumed_crashes", 0);
user_pref("browser.sessionstore.interval", 1000);
user_pref("browser.tabs.warnOnClose", false);
JS
    ff_bg "$1" about:blank
    sleep 8
    stop "$1" >/dev/null
}

# warm <profile>: fresh profile, running, with one about:blank window settled
warm() { fresh "$1"; cold "$1" about:blank >/dev/null; sleep 3; }

show() { sleep 2.5; python3 "$SPIKE_DIR/ffsession.py" "$WORK/$1"; }

make_pages "$PAGES" A1 A2 A3 B1 B2 C1 C2 D1 D2 D3 E1 E2 E3 F1 F2 F3 G1 G2
for n in $(seq 1 "$TRIALS"); do make_pages "$PAGES" "T${n}a" "T${n}b" "T${n}c"; done

echo "Firefox $(flatpak info "$APP" | awk '/Version:/ {print $2}'), work dir $WORK"

header 'FF1 cold, one call: --new-window A1 A2 A3'
echo 'expect: one window with A1..A3'
fresh ff1
echo "  ready after $(cold ff1 --new-window "$(url A1)" "$(url A2)" "$(url A3)")"
sleep 3; show ff1; stop ff1

header 'FF2 warm, one call: --new-window B1 B2'
echo 'expect: new window with B1 B2'
warm ff2
ff ff2 --new-window "$(url B1)" "$(url B2)"
show ff2; stop ff2

header 'FF3 warm, one call: --new-window C1 --new-tab C2'
echo 'expect: new window with C1 C2'
warm ff3
ff ff3 --new-window "$(url C1)" --new-tab "$(url C2)"
show ff3; stop ff3

header 'FF4 warm, sequential calls: --new-window D1; --new-tab D2; --new-tab D3'
echo 'expect: new window with D1 D2 D3'
warm ff4
t0=$(now_ms); ff ff4 --new-window "$(url D1)"; echo "  warm launcher returned in $(( $(now_ms) - t0 )) ms"
ff ff4 --new-tab "$(url D2)"
ff ff4 --new-tab "$(url D3)"
show ff4

header "FF5 warm, sequential calls, no delay, $TRIALS trials (same profile as FF4)"
ok=0
for n in $(seq 1 "$TRIALS"); do
    ff ff4 --new-window "$(url "T${n}a")"
    ff ff4 --new-tab "$(url "T${n}b")"
    ff ff4 --new-tab "$(url "T${n}c")"
    sleep 1
done
out=$(show ff4)
for n in $(seq 1 "$TRIALS"); do
    line=$(grep "'T${n}a'" <<<"$out")
    [[ $line == *"'T${n}b'"* && $line == *"'T${n}c'"* ]] && ok=$((ok + 1))
done
echo "  $ok/$TRIALS trials kept all three tabs in the new window"
stop ff4

header 'FF6 cold, plain URLs: E1 E2 E3 (no --new-window)'
echo 'expect: one window with E1..E3'
fresh ff6
echo "  ready after $(cold ff6 "$(url E1)" "$(url E2)" "$(url E3)")"
header 'FF7 immediately after FF6 is ready: --new-window F1; --new-tab F2; --new-tab F3'
echo 'expect: new window with F1..F3'
ff ff6 --new-window "$(url F1)"
ff ff6 --new-tab "$(url F2)"
ff ff6 --new-tab "$(url F3)"
show ff6; stop ff6

header 'FF8 race: two simultaneous cold launches of one stopped profile (G1, G2)'
echo 'expect: one browser process, two windows'
fresh ff8
ff_bg ff8 --new-window "$(url G1)"
ff_bg ff8 --new-window "$(url G2)"
sleep 8
echo "  browser main processes: $(main_pids ff8 | wc -l)"
show ff8
grep -h 'Address already in use\|already running' "$WORK/ff8.log" | sed 's/^/  log: /' | sort -u || true
stop ff8
