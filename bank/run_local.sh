#!/usr/bin/env bash
# Start the whole Sundai Bank demo on localhost, then kick off the live demo run.
#
#   bank/run_local.sh        # start everything, then start the demo run
#   bank/stop_local.sh       # stop everything
#
# Risk monitor :4318   Bank API :8000   Web app :5173   (+ mock gate :4319 if the monitor has no /gate/check)
# Logs: logs/local/<name>.log     PIDs: logs/local/<name>.pid
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"
LOGS="$ROOT/logs/local"
PY="$ROOT/.venv/bin/python"
RISK_PORT=4318 BANK_PORT=8000 WEB_PORT=5173 GATE_PORT=4319
mkdir -p "$LOGS"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# --- 1. setup ----------------------------------------------------------------
say "Setup"
if [ ! -x "$PY" ]; then
  if command -v uv >/dev/null 2>&1; then uv venv "$ROOT/.venv"; else python3 -m venv "$ROOT/.venv"; fi
fi
if command -v uv >/dev/null 2>&1; then
  uv pip install -q --python "$PY" -r bank/requirements.txt -r risk/requirements.txt
else
  "$PY" -m pip install -q -r bank/requirements.txt -r risk/requirements.txt
fi
[ -d web/node_modules ] || (cd web && npm install)

# --- 2. free the ports (only if it is an old copy of our own servers) ----------
# Prints the pid listening on a port, or nothing.
listener() { lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -n1 || true; }
is_ours() { # pid -> 0 if it is one of our servers started from this repo
  local cmd cwd
  cmd="$(ps -o command= -p "$1" 2>/dev/null || true)"
  cwd="$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n1 || true)"
  case "$cmd" in
    *risk.api.server*|*bank.api.server*|*bank.sim.mock_gate*|*vite*) ;;
    *) return 1 ;;
  esac
  case "$cwd" in "$ROOT"|"$ROOT"/*) return 0 ;; *) return 1 ;; esac
}
free_port() {
  local pid; pid="$(listener "$1")"
  [ -n "$pid" ] || return 0
  if is_ours "$pid"; then
    echo "port $1: old copy of our server (pid $pid) - stopping it"
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do [ -z "$(listener "$1")" ] && return 0; sleep 0.5; done
    kill -9 "$pid" 2>/dev/null || true
  else
    die "port $1 is in use by something else: $(ps -o pid=,command= -p "$pid"). Not touching it."
  fi
}
say "Checking ports"
for p in $RISK_PORT $BANK_PORT $WEB_PORT $GATE_PORT; do free_port "$p"; done

wait_for() { # url name
  for _ in $(seq 1 60); do curl -fsS -o /dev/null "$1" 2>/dev/null && { echo "$2 is up"; return 0; }; sleep 0.5; done
  die "$2 did not come up at $1 - see $LOGS"
}
start() { # name cmd... (run from repo root)
  local name="$1"; shift
  nohup "$@" >"$LOGS/$name.log" 2>&1 &
  echo $! >"$LOGS/$name.pid"
}

# --- 3. servers ---------------------------------------------------------------
say "Risk monitor on :$RISK_PORT"
start risk "$PY" -m uvicorn risk.api.server:app --port "$RISK_PORT"
wait_for "http://localhost:$RISK_PORT/docs" "risk monitor"

if curl -fsS "http://localhost:$RISK_PORT/openapi.json" | grep -q '"/gate/check"'; then
  GATE_KIND=real; GATE_URL="http://localhost:$RISK_PORT"
else
  GATE_KIND=mock; GATE_URL="http://localhost:$GATE_PORT"
  say "Risk monitor has no POST /gate/check - starting the MOCK gate on :$GATE_PORT"
  start gate "$PY" -m bank.sim.mock_gate --port "$GATE_PORT"
  for _ in $(seq 1 30); do
    curl -s -o /dev/null -X POST "$GATE_URL/gate/check" -d '{}' && break; sleep 0.5
  done
fi

say "Bank API on :$BANK_PORT (gate: $GATE_KIND at $GATE_URL)"
OTEL_ENABLED=1 OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:$RISK_PORT" \
GATE_ENABLED=1 RISK_API_URL="$GATE_URL" \
  start bank "$PY" -m uvicorn bank.api.server:app --port "$BANK_PORT"
wait_for "http://localhost:$BANK_PORT/agents" "bank API"

say "Web app on :$WEB_PORT"
start web npm --prefix web run dev
wait_for "http://localhost:$WEB_PORT/" "web app"

# --- 4. live demo run ---------------------------------------------------------
say "Starting the live demo run (seed 154, payments + kyc drifting, gate on)"
RUN="$(curl -fsS -X POST "http://localhost:$BANK_PORT/sim/demo")"
echo "$RUN"

cat <<EOF

Sundai Bank is running.
  Customer bank site : http://localhost:$WEB_PORT/
  Staff console      : http://localhost:$WEB_PORT/#staff
  Risk dashboard     : http://localhost:$RISK_PORT/dashboard
  Gate               : $GATE_KIND ($GATE_URL/gate/check)
  Logs               : $LOGS/*.log
Stop with: bank/stop_local.sh
EOF
