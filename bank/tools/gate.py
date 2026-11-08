"""Pre-action risk gate client.

Before a high-impact action runs, the proposed event goes to the risk monitor, which answers
run / log / review / pause. `review` and `pause` stop the action (the wrapper logs it as 'blocked').

The monitor in risk/api has no pre-action endpoint yet. This client targets the one we need
(see bank/README.md, "What the gate needs from the risk team"):

    POST {RISK_API_URL}/gate/check
    request : {"event": <log event without pressure_mode and outcome>, "history": [<earlier actions this session>]}
    response: {"decision": "run"|"log"|"review"|"pause", "risk": 0.0-1.0, "reason": "..."}

If the monitor is unreachable, answers non-2xx, or answers nonsense, the gate fails open: a
warning is logged and the action proceeds.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("bank.gate")

DECISIONS = ("run", "log", "review", "pause")
STOPS = frozenset({"review", "pause"})


@dataclass
class GateDecision:
    decision: str = "run"
    risk: float | None = None
    reason: str = ""
    reachable: bool = True


class RiskGate(Protocol):
    def check(self, event: dict, history: list[dict]) -> GateDecision: ...


class NoGate:
    def check(self, event: dict, history: list[dict]) -> GateDecision:
        return GateDecision("run", reason="gate disabled")


class HttpRiskGate:
    def __init__(self, base_url: str | None = None, timeout: float = 2.0):
        self.base_url = (base_url or os.environ.get("RISK_API_URL")
                         or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "http://localhost:4318").rstrip("/")
        self.url = f"{self.base_url}/gate/check"
        self.timeout = timeout
        self.failures = 0

    def check(self, event: dict, history: list[dict]) -> GateDecision:
        proposed = {k: v for k, v in event.items() if k not in ("pressure_mode", "outcome")}  # never leak the label
        body = json.dumps({"event": proposed, "history": history}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - fixed http(s) url from env
                data = json.loads(resp.read())
            decision = data.get("decision")
            if decision not in DECISIONS:
                raise ValueError(f"unknown decision {decision!r}")
            return GateDecision(decision, data.get("risk"), str(data.get("reason", "")))
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            self.failures += 1
            if self.failures == 1 or self.failures % 50 == 0:
                log.warning("risk gate unavailable (%s: %s); failing open [%d failure(s)]", self.url, exc, self.failures)
            return GateDecision("run", reason=f"gate unavailable: {exc}", reachable=False)
