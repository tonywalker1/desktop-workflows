# shellcheck shell=bash
# Shared helpers for the launching spike. Source this file; do not execute it.
# See docs/design/launching.md for what the experiments establish and why.

set -euo pipefail

# shellcheck disable=SC2034  # used by the scripts that source this file
SPIKE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# now_ms: monotonic-enough wall clock in milliseconds
now_ms() { echo $(( $(date +%s%N) / 1000000 )); }

# require_flatpak <app-id>: abort unless the Flatpak app is installed
require_flatpak() {
    flatpak info "$1" >/dev/null 2>&1 || { echo "SKIP: Flatpak $1 is not installed" >&2; exit 77; }
}

# make_pages <dir> <name>...: one tiny HTML page per name, titled DWF-<name>
make_pages() {
    local dir=$1; shift
    mkdir -p "$dir"
    local n
    for n in "$@"; do
        printf '<!doctype html><title>DWF-%s</title><h1>%s</h1>\n' "$n" "$n" > "$dir/$n.html"
    done
}

# header <text>: section banner in the output
header() { printf '\n== %s\n' "$*"; }
