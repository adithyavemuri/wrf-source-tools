#!/usr/bin/env bash
# Real-WRF regression test for scoped SMS-3DTKE assignment/equation evidence.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SOURCE_ROOT="${1:-${PROJECT_ROOT}/../wrf-arw-local/sources/WRF-4.7.1-recursive}"
INDEX="${2:-${PROJECT_ROOT}/generated/fortran-index.json}"
GRAPH="${3:-${PROJECT_ROOT}/generated/sms-3dtke-graph.json}"
OUTPUT_PREFIX="${4:-${PROJECT_ROOT}/generated/sms-3dtke-equation-evidence}"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/sms-equation-test.XXXXXX")"

cleanup() {
    rm -rf -- "${TEST_TMP}"
}
trap cleanup EXIT

run_equations() {
    local prefix="$1"
    (cd -- "${PROJECT_ROOT}" && PYTHONPATH=src python3 -m wrf_source_tools equations \
        --index "${INDEX}" --graph "${GRAPH}" --source-root "${SOURCE_ROOT}" \
        --variable tke --variable tendency \
        --procedure tke_rhs --procedure tke_shear --procedure tke_buoyancy --procedure tke_dissip \
        --output-prefix "${prefix}")
}

status_before="$(git -C "${SOURCE_ROOT}" status --porcelain=v1 --untracked-files=all)"
run_equations "${TEST_TMP}/first"
run_equations "${TEST_TMP}/second"
cmp -s "${TEST_TMP}/first.json" "${TEST_TMP}/second.json"
cmp -s "${TEST_TMP}/first.md" "${TEST_TMP}/second.md"

python3 - "${TEST_TMP}/first.json" <<'PY'
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

path = Path(sys.argv[1])
evidence = json.loads(path.read_text())
assignments = evidence["assignments"]
assert evidence["generator_version"] == "1.0.0"
assert evidence["scheme"] == "SMS-3DTKE"
assert evidence["variables"] == ["tendency", "tke"]
assert evidence["procedure_filter"] == ["tke_buoyancy", "tke_dissip", "tke_rhs", "tke_shear"]
assert evidence["assignment_count"] == evidence["parsed_assignment_count"] == len(assignments) == 50
assert evidence["unparsed_assignment_count"] == 0
assert len({item["evidence_id"] for item in assignments}) == 50
assert all(item["source_sha256"] and item["rhs_ir"] for item in assignments)
assert sum(bool(item["loops"]) for item in assignments) == 40
assert sum(bool(item["runtime_conditions"]) for item in assignments) == 27
assert Counter(item["procedure"] for item in assignments) == {
    "tke_rhs": 1, "tke_buoyancy": 10, "tke_dissip": 8, "tke_shear": 31,
}
tendency = [item for item in assignments if item["target"]["base_name"] == "tendency"]
assert len(tendency) == 16
assert all(item["assignment_class"] == "self_update" for item in tendency)
assert all(item["target_declaration"]["intent"] == "inout" for item in tendency)
assert {item["span"]["start_line"] for item in tendency} >= {6224, 6319, 6328, 6518, 6681}
assert len(evidence["call_links"]) == 4
assert all(item["confidence"] == "high" for item in evidence["call_links"])
assert path.stat().st_size < 300_000
PY

status_after="$(git -C "${SOURCE_ROOT}" status --porcelain=v1 --untracked-files=all)"
[[ "${status_before}" == "${status_after}" ]]
mkdir -p -- "$(dirname -- "${OUTPUT_PREFIX}")"
cp -- "${TEST_TMP}/first.json" "${OUTPUT_PREFIX}.json"
cp -- "${TEST_TMP}/first.md" "${OUTPUT_PREFIX}.md"
printf 'SMS-3DTKE equation evidence test: PASS\n'
printf 'JSON: %s.json\n' "${OUTPUT_PREFIX}"
printf 'Report: %s.md\n' "${OUTPUT_PREFIX}"
