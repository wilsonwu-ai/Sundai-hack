"""The one wrapper every tool call goes through.

For each call it: (1) assigns the step number, (2) computes policy_check from the session history,
(3) runs the risk gate for high-impact actions, (4) executes the tool unless blocked, (5) appends one
validated JSON line to events.jsonl, (6) emits an OpenTelemetry span.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from bank.db.bank import Bank
from bank.tools import REGISTRY, policy, reasoning
from bank.tools.gate import STOPS, GateDecision, NoGate, RiskGate
from bank.tools.telemetry import Telemetry
from bank.tools.types import HIGH_IMPACT, Result, Session, ToolError
from bank.tools.validate import validate_event

log = logging.getLogger("bank.runtime")

FIELD_ORDER = ("agent_id", "agent_type", "session_id", "step", "timestamp", "action", "params", "auth_level",
               "policy_check", "outcome", "pressure_mode", "reasoning_summary")


class SimClock:
    """Simulated time: fixed start, advanced only by seeded amounts."""

    def __init__(self, start: datetime):
        self.now = start

    def advance(self, seconds: int) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now

    def stamp(self) -> str:
        return self.now.strftime("%Y-%m-%dT%H:%M:%SZ")


class EventSink:
    """Append-only events.jsonl. Same events in, same bytes out."""

    def __init__(self, path: Path | None):
        self.path = path
        self._fh = None
        self.count = 0
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(path, "a", encoding="utf-8", newline="\n")  # noqa: SIM115

    def write(self, event: dict) -> None:
        self.count += 1
        if self._fh:
            self._fh.write(json.dumps(event, ensure_ascii=True) + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


class ToolRuntime:
    def __init__(self, bank: Bank, clock: SimClock, sink: EventSink, telemetry: Telemetry | None = None,
                 gate: RiskGate | None = None, gate_enabled: bool = False, lock: threading.RLock | None = None):
        self.bank = bank
        self.clock = clock
        self.sink = sink
        self.telemetry = telemetry or Telemetry(False)
        self.gate = gate or NoGate()
        self.gate_enabled = gate_enabled
        self.lock = lock or bank.lock
        self.gate_calls = 0
        self.blocked_count = 0
        self.listeners: list = []          # callables(event) for the API / live feed

    # -- sessions ---------------------------------------------------------------

    def start_session(self, s: Session) -> None:
        self.telemetry.start_session(s, self.clock.stamp())

    def end_session(self, s: Session) -> None:
        s.ended = True
        self.telemetry.end_session(s)

    # -- the wrapper ------------------------------------------------------------

    def call(self, s: Session, action: str, params: dict) -> Result:
        with self.lock:
            return self._call(s, action, params)

    def _call(self, s: Session, action: str, params: dict) -> Result:
        tool = REGISTRY[s.agent_type].get(action)
        if tool is None:
            raise ValueError(f"{s.agent_type} has no action {action!r}")
        now = self.clock.advance(s.rng.randint(15, 140))
        s.step += 1                                                              # 1. step number
        policy_check = policy.evaluate(self.bank, s, action, params, now)         # 2. policy_check
        auth = self._auth_level(s, action, params)
        summary = reasoning.describe(self.bank, s, action, params)
        event = {
            "agent_id": s.agent_id, "agent_type": s.agent_type, "session_id": s.session_id, "step": s.step,
            "timestamp": self.clock.stamp(), "action": action, "params": params, "auth_level": auth,
            "policy_check": policy_check, "outcome": "success", "pressure_mode": s.pressure,
            "reasoning_summary": summary,
        }

        decision: GateDecision | None = None
        if self.gate_enabled and action in HIGH_IMPACT:                          # 3. risk gate
            self.telemetry.flush()
            self.gate_calls += 1
            decision = self.gate.check(event, [{"step": h["step"], "action": h["action"],
                                                "policy_check": h["policy_check"], "outcome": h["outcome"]}
                                               for h in s.history])

        data: dict = {}
        if decision is not None and decision.decision in STOPS:                  # blocked: the action does not run
            event["outcome"] = "blocked"
            self.blocked_count += 1
            s.blocked = True
        else:
            try:                                                                 # 4. execute
                data = tool(self.bank, s, params, now)
                self.bank.commit()
            except (ToolError, KeyError, ValueError, TypeError) as exc:
                self.bank.conn.rollback()
                event["outcome"] = "error"
                data = {"error": str(exc)}

        ordered = {k: event[k] for k in FIELD_ORDER}
        validate_event(ordered)
        self.sink.write(ordered)                                                 # 5. events.jsonl
        s.last_ts = ordered["timestamp"]
        self.telemetry.emit(s, ordered)                                          # 6. OTel span
        s.history.append({"step": s.step, "action": action, "params": params, "outcome": ordered["outcome"],
                          "policy_check": policy_check, "data": data})
        if s.blocked:
            self.end_session(s)
        for fn in self.listeners:
            fn(ordered)
        return Result(action, ordered["outcome"], policy_check, data, now,
                      decision.decision if decision else None, decision.reason if decision else None, ordered)

    @staticmethod
    def _auth_level(s: Session, action: str, params: dict) -> str:
        if s.agent_type == "support" and params.get("customer_id") != s.customer_id:
            return "none"                    # that customer never verified themselves in this session
        if action == "file_sar":
            return "elevated"                # filing a SAR needs elevated rights (see shared/sample_logs)
        if s.agent_type == "disputes" and action in ("open_dispute", "fetch_txn"):
            return "customer_verified"       # intake and look-ups run on the customer's own verification
        return s.auth_level
