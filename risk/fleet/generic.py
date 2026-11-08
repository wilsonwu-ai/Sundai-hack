"""A small generic scorer for categories that have no detector of their own.

The team's SurpriseSeverityDetector cannot run here without BehaviorGPT (it imports the
BehaviorGPT client at module load), so the eight team categories use this stand-in scorer:

- a Markov next-action model of order 2 with backoff (agent 9's MarkovModel), fitted on
  normal sessions only, token = action + auth level + policy check;
- the team's severity() table (risk/scoring/severity.py);
- one rule flag: a skipped policy check.

risk of a step = surprise x severity + 0.3 per flag, capped at 1. The pause line is set on
held-out normal sessions so that at most the pause budget of them is paused.
Step k is scored from steps 1..k only. Labels and message text never reach this module.
"""

from __future__ import annotations

import math

from risk.scoring.severity import severity
from risk.wilson_cs.detector import MarkovModel

FLAG_WEIGHT = 0.3
DECISION_ORDER = {"run": 0, "log": 1, "review": 2, "pause": 3}


def _token(event: dict) -> str:
    return f"{event['action']}|auth={event['auth_level']}|pol={event['policy_check']}"


class GenericDetector:
    name = "generic_markov"

    def __init__(self, order: int = 2, norm_bits: float = 8.0, min_pause_line: float = 0.25):
        self.order = order
        self.norm_bits = norm_bits
        self.model = MarkovModel(order)
        self.min_pause_line = min_pause_line
        self.pause_line = 0.5
        self.log_line = 0.2
        self.calibration: dict = {}

    def fit(self, sessions: list[list[dict]]) -> "GenericDetector":
        """Fit the next-action model. Pass NORMAL sessions only."""
        self.model.fit([[_token(e) for e in s] for s in sessions])
        return self

    def calibrate(self, normal_sessions: list[list[dict]], pause_budget: float = 0.05) -> dict:
        """Set the pause line so that at most pause_budget of these normal sessions get a pause."""
        n = len(normal_sessions)
        if n == 0:
            return {}
        top = sorted((max(s["risk"] for s in self._score_steps(x)) for x in normal_sessions), reverse=True)
        k = int(math.floor(pause_budget * n))
        line = math.nextafter(top[k], math.inf) if k < n else 0.0
        self.pause_line = max(line, self.min_pause_line)
        self.log_line = 0.4 * self.pause_line
        self.calibration = {"n_sessions": n, "pause_line": self.pause_line, "log_line": self.log_line}
        return self.calibration

    def _step_risk(self, surprise: float, severity_: float, flags: list[str]) -> float:
        return min(1.0, surprise * severity_ + FLAG_WEIGHT * len(flags))

    def _score_steps(self, events: list[dict]) -> list[dict]:
        tokens = [_token(e) for e in events]
        steps = []
        for i, e in enumerate(events):
            bits = self.model.surprise_bits(tokens[max(0, i - self.order):i], tokens[i])
            surprise = min(1.0, bits / self.norm_bits)
            sev, reasons = severity(e)
            flags = ["skipped_policy"] if e["policy_check"] == "skipped" else []
            steps.append({
                "step": e["step"], "action": e["action"], "surprise": round(surprise, 6),
                "severity": round(sev, 6), "severity_reasons": reasons, "flags": flags,
                "risk": round(self._step_risk(surprise, sev, flags), 6),
            })
        return steps

    def score_session(self, events: list[dict]) -> dict:
        steps = self._score_steps(events)
        for s in steps:
            s["decision"] = "pause" if s["risk"] >= self.pause_line else ("log" if s["risk"] >= self.log_line else "run")
        decision = max((s["decision"] for s in steps), key=DECISION_ORDER.get, default="run")
        return {"risk": max((s["risk"] for s in steps), default=0.0), "decision": decision, "steps": steps}

    def score_agent(self, agent_id: str, sessions: dict[str, list[dict]]) -> dict:
        scored = {sid: self.score_session(ev) for sid, ev in sessions.items()}
        paused = sum(1 for s in scored.values() if s["decision"] == "pause")
        return {"agent_id": agent_id, "risk": max((s["risk"] for s in scored.values()), default=0.0),
                "flagged": paused > 0, "paused_sessions": paused, "sessions": scored}
