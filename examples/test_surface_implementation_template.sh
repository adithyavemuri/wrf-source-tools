#!/usr/bin/env bash
# Real-WRF regression test for the surface-scheme implementation comparator.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
WRF_ROOT="${1:-${PROJECT_ROOT}/../wrf-arw-local/sources/WRF-4.7.1-recursive}"
INDEX="${2:-${PROJECT_ROOT}/generated/fortran-index.json}"
OUTPUT_PREFIX="${3:-${PROJECT_ROOT}/generated/surface-scheme-implementation-template}"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/surface-template-test.XXXXXX")"

cleanup() {
    rm -rf -- "${TEST_TMP}"
}
trap cleanup EXIT

run_template() {
    local prefix="$1"
    (cd -- "${PROJECT_ROOT}" && PYTHONPATH=src python3 -m wrf_source_tools implementation-template \
        --index "${INDEX}" \
        --source-root "${WRF_ROOT}" \
        --adapter "${PROJECT_ROOT}/src/wrf_source_tools/adapters/wrf.json" \
        --component surface-layer \
        --reference MYNN \
        --reference revised-MM5 \
        --new-name my_surface_scheme \
        --output-prefix "${prefix}")
}

status_before="$(git -C "${WRF_ROOT}" status --porcelain=v1 --untracked-files=all)"
run_template "${TEST_TMP}/first"
run_template "${TEST_TMP}/second"
cmp -s "${TEST_TMP}/first.json" "${TEST_TMP}/second.json"
cmp -s "${TEST_TMP}/first.md" "${TEST_TMP}/second.md"

python3 - "${TEST_TMP}/first.json" <<'PY'
import json
import sys

template = json.load(open(sys.argv[1]))
assert template["generator_version"] == "1.0.0"
assert template["component"] == "surface-layer"
assert template["new_scheme_name"] == "my_surface_scheme"
assert len(template["implementation_chain"]) == 8

mynn, mm5 = template["references"]
assert (mynn["key"], mm5["key"]) == ("mynn", "revised-mm5")
assert mynn["selector"] == {"name": "sf_sfclay_physics", "value": "5"}
assert mm5["selector"] == {"name": "sf_sfclay_physics", "value": "1"}
assert mynn["symbolic_constant"]["status"] == mm5["symbolic_constant"]["status"] == "found"
assert mynn["registry_package"]["status"] == mm5["registry_package"]["status"] == "found"
assert len(mynn["namelist_option"]["evidence"]) == len(mm5["namelist_option"]["evidence"]) == 1
assert mynn["primary_interface"]["argument_count"] == 85
assert mm5["primary_interface"]["argument_count"] == 86
assert len(mynn["driver"]["entry_calls"]) == len(mm5["driver"]["entry_calls"]) == 2
assert all(call["resolution_status"] == "resolved" and call["confidence"] == "high" for ref in (mynn, mm5) for call in ref["driver"]["entry_calls"])
assert mynn["initialization"]["status"] == "found"
assert mynn["initialization"]["calls"][0]["caller"] == "bl_init"
assert mm5["initialization"]["status"] == "not_applicable"
assert len(mynn["compatibility_check_candidates"]) == 1
assert len(mm5["compatibility_check_candidates"]) == 2

comparison = template["interface_comparisons"][0]
assert comparison["common_argument_count"] == 76
assert len(comparison["only_left"]) == 9
assert len(comparison["only_right"]) == 10
assert len(comparison["position_differences"]) == 55
assert len(comparison["intent_differences"]) == 2
PY

status_after="$(git -C "${WRF_ROOT}" status --porcelain=v1 --untracked-files=all)"
[[ "${status_before}" == "${status_after}" ]]

mkdir -p -- "$(dirname -- "${OUTPUT_PREFIX}")"
cp -- "${TEST_TMP}/first.json" "${OUTPUT_PREFIX}.json"
cp -- "${TEST_TMP}/first.md" "${OUTPUT_PREFIX}.md"
printf 'Surface implementation-template test: PASS\n'
printf 'JSON: %s.json\n' "${OUTPUT_PREFIX}"
printf 'Guide: %s.md\n' "${OUTPUT_PREFIX}"
