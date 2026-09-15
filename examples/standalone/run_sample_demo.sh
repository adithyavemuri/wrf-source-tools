#!/usr/bin/env bash
set -Eeuo pipefail

readonly EXAMPLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$EXAMPLE_DIR/../.." && pwd -P)"
readonly SOURCE_DIR="$EXAMPLE_DIR/sample_wrf"
readonly OUTPUT_DIR="$PROJECT_DIR/generated/sample-demo"

mkdir -p "$OUTPUT_DIR"
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

python3 -m wrf_source_tools extract \
  --wrf "$SOURCE_DIR" \
  --output "$OUTPUT_DIR/fortran-index.json"

python3 -m wrf_source_tools graph \
  --index "$OUTPUT_DIR/fortran-index.json" \
  --scheme DEMO_PBL \
  --root pbl_driver \
  --root-path phys/module_pbl_driver.F90 \
  --entry demo_pbl \
  --set bl_pbl_physics=5 \
  --variable qke \
  --max-depth 4 \
  --output-prefix "$OUTPUT_DIR/demo-pbl"

printf 'Sample demonstration complete. Outputs: %s\n' "$OUTPUT_DIR"
