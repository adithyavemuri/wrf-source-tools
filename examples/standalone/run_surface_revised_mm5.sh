#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s /path/to/WRF\n' "$0" >&2
  exit 2
fi

readonly EXAMPLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec "$EXAMPLE_DIR/run_scheme_graph.sh" \
  "$1" surface-revised-mm5 REVISED_MM5 \
  surface_driver phys/module_surface_driver.F sfclayrev - \
  sf_sfclay_physics=1 sfclayrevscheme=1

