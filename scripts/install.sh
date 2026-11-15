#!/usr/bin/env bash
# Install dwf for the current user as a single-file Python zipapp, then generate launcher entries for existing tasks.
# dwf uses only the Python standard library, so nothing else is installed.
#
# Usage: scripts/install.sh [--uninstall]
# Environment: DWF_BIN_DIR (default ~/.local/bin)
#
# Uninstalling removes the executable and the launcher entries dwf generated. Task files in ~/.config/dwf and logs in
# ~/.local/state/dwf are left in place.

set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BIN_DIR=${DWF_BIN_DIR:-$HOME/.local/bin}
TARGET=$BIN_DIR/dwf

if [ "${1:-}" = --uninstall ]; then
    if [ -x "$TARGET" ]; then
        "$TARGET" sync --remove-all
        rm -f -- "$TARGET"
        echo "removed $TARGET"
    else
        echo "dwf is not installed at $TARGET"
    fi
    exit 0
fi

python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    || { echo "dwf needs Python 3.11 or newer (found $(python3 --version))" >&2; exit 1; }

mkdir -p "$BIN_DIR"
python3 -I - "$REPO/src" "$TARGET" <<'PY'
import pathlib
import sys
import zipapp

src, target = (pathlib.Path(p) for p in sys.argv[1:3])
tmp = target.with_name('.dwf.tmp')
zipapp.create_archive(src, tmp, interpreter='/usr/bin/env python3', main='dwf.cli:entry', compressed=True,
                      filter=lambda p: '__pycache__' not in p.parts and p.suffix != '.pyc')
tmp.chmod(0o755)
tmp.replace(target)
PY
echo "installed $TARGET ($("$TARGET" --version))"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "note: $BIN_DIR is not on PATH; launcher entries still work (they use the absolute path)" ;;
esac

"$TARGET" sync
