#!/usr/bin/env bash
set -Eeuo pipefail

if (($# < 7)); then
  cat >&2 <<'EOF'
Usage:
  run_scheme_graph.sh WRF_DIR OUTPUT_NAME SCHEME ROOT ROOT_PATH ENTRY VARIABLE [NAME=VALUE ...]

Use '-' for ENTRY or VARIABLE to omit that filter.
EOF
  exit 2
fi

readonly EXAMPLE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$EXAMPLE_DIR/../.." && pwd -P)"
readonly WRF_SOURCE="$(cd -- "$1" && pwd -P)"
readonly OUTPUT_NAME="$2"
readonly SCHEME="$3"
readonly ROOT_PROCEDURE="$4"
readonly ROOT_PATH="$5"
readonly ENTRY="$6"
readonly VARIABLE="$7"
shift 7

[[ "$OUTPUT_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || {
  printf 'OUTPUT_NAME may contain only letters, numbers, dot, underscore, and hyphen.\n' >&2
  exit 2
}

readonly OUTPUT_DIR="$PROJECT_DIR/generated/$OUTPUT_NAME"
mkdir -p "$OUTPUT_DIR"
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

python3 -m wrf_source_tools extract \
  --wrf "$WRF_SOURCE" \
  --output "$OUTPUT_DIR/fortran-index.json"

graph_command=(
  python3 -m wrf_source_tools graph
  --index "$OUTPUT_DIR/fortran-index.json"
  --scheme "$SCHEME"
  --root "$ROOT_PROCEDURE"
  --root-path "$ROOT_PATH"
  --max-depth 8
  --wrf "$WRF_SOURCE"
  --expand-dependencies
  --output-prefix "$OUTPUT_DIR/$OUTPUT_NAME"
)
[[ "$ENTRY" == - ]] || graph_command+=(--entry "$ENTRY")
[[ "$VARIABLE" == - ]] || graph_command+=(--variable "$VARIABLE")
for setting in "$@"; do
  [[ "$setting" == *=* ]] || { printf 'Invalid setting: %s (expected NAME=VALUE)\n' "$setting" >&2; exit 2; }
  graph_command+=(--set "$setting")
done

"${graph_command[@]}"
printf 'Graph complete:\n  %s.json\n  %s.svg\n' "$OUTPUT_DIR/$OUTPUT_NAME" "$OUTPUT_DIR/$OUTPUT_NAME"

