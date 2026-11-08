"""A stand-in risk gate for rehearsing the demo before the risk team's /gate/check exists.

    .venv/bin/python -m bank.sim.mock_gate --port 4319
    RISK_API_URL=http://localhost:4319 GATE_ENABLED=1 bank/sim/demo.sh

It speaks the contract in bank/README.md ("What the gate needs from the risk team") and decides with one
crude rule: pause a high-impact action whose policy check was skipped or failed. It is NOT the risk model.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/gate/check":
            self.send_error(404)
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        ev = body.get("event", {})
        bad = ev.get("policy_check") in ("skipped", "failed")
        out = {
            "decision": "pause" if bad else "run",
            "risk": 0.92 if bad else 0.08,
            "reason": f"{ev.get('action')} went ahead with policy check '{ev.get('policy_check')}'" if bad else "routine",
        }
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", type=int, default=4319)
    args = p.parse_args()
    print(f"mock gate on http://localhost:{args.port}/gate/check")
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
