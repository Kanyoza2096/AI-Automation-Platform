#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python scripts/production_certification_gate.py
python -m pytest -q \
  tests/test_production_certification_gate.py \
  tests/test_v72_0_production_execution_certification.py

if [[ "${KDO_RUN_LIVE_CERTIFICATION:-0}" == "1" ]]; then
  python scripts/staging_production_certification.py --live
else
  echo "LIVE_CERTIFICATION=NOT_RUN"
  echo "Set KDO_RUN_LIVE_CERTIFICATION=1 with staging credentials to exercise real PostgreSQL/Supabase RPCs."
fi
