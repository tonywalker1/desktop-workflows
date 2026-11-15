#!/usr/bin/env bash
# Which cgroup does a natively packaged app land in when launched through its .desktop file?
#
# S1 launches with plain `gio launch`; S2 wraps the same call in a transient systemd user scope. Only processes
# started by this script are killed afterwards; an instance the user already had open is left alone.
#
# Usage: spikes/launching/scopes.sh [desktop-id]     (default org.gnome.Calculator; must be a native, non-D-Bus-
#        activated app whose Exec binary keeps running)

source "$(dirname "$0")/lib.sh"

ID=${1:-org.gnome.Calculator}

desktop_file() {
    local d
    for d in "${XDG_DATA_HOME:-$HOME/.local/share}" ${XDG_DATA_DIRS//:/ }; do
        [ -f "$d/applications/$1.desktop" ] && { echo "$d/applications/$1.desktop"; return 0; }
    done
    return 1
}

FILE=$(desktop_file "$ID") || { echo "SKIP: $ID.desktop not found on XDG_DATA_DIRS" >&2; exit 77; }
BIN=$(sed -n 's/^Exec=\([^ ]*\).*/\1/p' "$FILE" | head -1)
PATTERN="^(/usr/bin/)?$(basename "$BIN")( |$)"
STARTED=()

cleanup() { [ ${#STARTED[@]} -gt 0 ] && kill "${STARTED[@]}" 2>/dev/null; true; }
trap cleanup EXIT

# launch_and_report <cmd>...: run the launcher, then report the new process and its cgroup
launch_and_report() {
    local before t0 pid ppid
    before=$(pgrep -f "$PATTERN" | sort || true)
    t0=$(now_ms)
    "$@"
    echo "  launcher rc=$? after $(( $(now_ms) - t0 )) ms"
    sleep 2
    pid=$(comm -13 <(echo "$before") <(pgrep -f "$PATTERN" | sort) | head -1)
    [ -z "$pid" ] && { echo '  no new process found'; return; }
    STARTED+=("$pid")
    ppid=$(ps -o ppid= -p "$pid" | tr -d ' ')
    echo "  pid=$pid ppid=$ppid ($(ps -o comm= -p "$ppid"))"
    echo "  cgroup: $(cut -d: -f3 "/proc/$pid/cgroup")"
}

echo "app: $FILE"
echo "this script's cgroup: $(cut -d: -f3 /proc/self/cgroup)"

header 'S1 gio launch'
launch_and_report gio launch "$FILE"

header 'S2 systemd-run --user --scope ... gio launch'
UNIT=app-dwf-spike-$$.scope
launch_and_report systemd-run --user --quiet --scope --collect --slice=app.slice --unit="$UNIT" gio launch "$FILE"
echo "  $UNIT: $(systemctl --user is-active "$UNIT")"
