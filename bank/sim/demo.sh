#!/usr/bin/env bash
# Demo preset: a live run (about 2 events/second) with a fixed seed, Payments and KYC drifting,
# OpenTelemetry on and the risk gate on. A drifting Payments agent tries to pay a new payee $9,800
# without 2FA, and the risk monitor gets the chance to pause it.
#
#   bank/sim/demo.sh                 # run the demo
#   bank/sim/demo.sh --live-rate 6   # extra flags go to run_sim (faster)
#
# Start the risk monitor first (port 4318) so spans arrive and the gate is called:
#   .venv/bin/uvicorn risk.api.server:app --port 4318
# Seed 154 was picked so both drifters start early in the run and drift hard; the "Run demo" button in the
# staff console starts the same preset.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-.venv/bin/python}"
export OTEL_ENABLED=1 GATE_ENABLED=1
export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4318}"
exec "$PY" -m bank.sim.run_sim --seed 154 --force-drifters payments,kyc --live --demo "$@"
