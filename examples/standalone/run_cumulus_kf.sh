#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s /path/to/WRF\n' "$0" >&2
  exit 2
fi

readonly EXAMPLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$EXAMPLE_DIR/run_scheme_graph.sh" \
  "$1" cumulus-kain-fritsch KAIN_FRITSCH \
  cumulus_driver phys/module_cumulus_driver.F kf_eta_cps - \
  cu_physics=1 kfetascheme=1

