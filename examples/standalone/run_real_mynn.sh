#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s /path/to/WRF\n' "$0" >&2
  exit 2
fi

readonly EXAMPLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$EXAMPLE_DIR/../.." && pwd -P)"
readonly WRF_SOURCE="$(cd -- "$1" && pwd -P)"
readonly OUTPUT_DIR="$PROJECT_DIR/generated/real-mynn"

mkdir -p "$OUTPUT_DIR"
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

python3 -m wrf_source_tools extract --wrf "$WRF_SOURCE" --output "$OUTPUT_DIR/fortran-index.json"
python3 -m wrf_source_tools graph \
  --index "$OUTPUT_DIR/fortran-index.json" \
  --scheme MYNN \
  --root pbl_driver \
  --root-path phys/module_pbl_driver.F \
  --set bl_pbl_physics=5 \
  --set mynnpblscheme=5 \
  --variable qke \
  --max-depth 8 \
  --output-prefix "$OUTPUT_DIR/mynn"

printf 'WRF MYNN analysis complete. Outputs: %s\n' "$OUTPUT_DIR"
