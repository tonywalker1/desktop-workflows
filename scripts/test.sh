#!/usr/bin/env bash
# Run the unit tests (standard-library unittest; nothing to install). Extra arguments go to unittest.
# Usage: scripts/test.sh [-v] [-k PATTERN]

set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO"
PYTHONPATH=src:tests exec python3 -m unittest discover -s tests -t tests "$@"
