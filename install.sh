#!/usr/bin/env bash
# ./install.sh --check only runs the compatibility check, anything else goes to r7harness install
set -euo pipefail

repository_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
launcher="$repository_root/bin/r7harness"

if [[ "${1:-}" == "--check" ]]; then
  shift
  exec "$launcher" check "$@"
fi

exec "$launcher" install "$@"
