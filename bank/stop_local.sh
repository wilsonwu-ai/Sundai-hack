#!/usr/bin/env bash
# Stop everything bank/run_local.sh started (risk monitor, mock gate, bank API, web app).
# Only stops processes that are our own servers started from this repo.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"
LOGS="$ROOT/logs/local"

listener() { lsof -nP -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -n1 || true; }
is_ours() {
  local cmd cwd
  cmd="$(ps -o command= -p "$1" 2>/dev/null || true)"
  cwd="$(lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n1 || true)"
  case "$cmd" in
    *risk.api.server*|*bank.api.server*|*bank.sim.mock_gate*|*vite*) ;;
    *) return 1 ;;
  esac
  case "$cwd" in "$ROOT"|"$ROOT"/*) return 0 ;; *) return 1 ;; esac
}

# 1. the wrappers we started (e.g. the `npm run dev` parent of vite)
for f in "$LOGS"/*.pid; do
  [ -f "$f" ] || continue
  pid="$(cat "$f")"
  kill "$pid" 2>/dev/null && echo "stopped $(basename "$f" .pid) (pid $pid)"
  rm -f "$f"
done

# 2. whatever still holds our ports
for port in 4318 8000 5173 4319; do
  pid="$(listener "$port")"
  [ -n "$pid" ] || continue
  if is_ours "$pid"; then
    kill "$pid" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do [ -z "$(listener "$port")" ] && break; sleep 0.5; done
    [ -n "$(listener "$port")" ] && kill -9 "$pid" 2>/dev/null
    echo "stopped :$port (pid $pid)"
  else
    echo "port $port is held by something that is not ours (pid $pid) - left alone"
  fi
done
echo "done"
