#!/bin/bash
# Interactive Docker container with all necessary mounts
# This script provides the same mounting setup as full_flow_orchestrator.py

set -e
ROOT="$(pwd)"
OUT="${ROOT}/outputs"
WRK="${ROOT}/workloads"
mkdir -p "$OUT"
echo "Starting interactive container..."
cmd=(docker run --rm -it -w /flow \
  -v "$OUT:/outputs" \
  -v "$ROOT/flow:/flow" \
  -v "$ROOT/utils:/flow/utils" \
  -v "$ROOT/environment:/environment"\
  -v "$ROOT/riscv-copy:/riscv-copy")
if [ -d "$WRK" ]; then
  cmd+=( -v "$WRK:/workloads" )
fi
cmd+=( riscv-perf-model:olympia bash )
"${cmd[@]}"
echo "Done. Outputs in: $OUT"
