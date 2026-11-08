"""Shared types for sessions, tool calls and agent policies."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

HIGH_IMPACT = frozenset({
    "send_ach", "send_wire", "issue_credit", "open_account", "waive_fee",
    "dismiss_alert", "decide_loan", "post_adjustment", "request_access",
})


class ToolError(Exception):
    """A tool failed to run (bad id, insufficient funds, ...). Logged as outcome 'error'."""


@dataclass
class Session:
    session_id: str
    agent_id: str
    agent_type: str
    customer_id: str | None
    auth_level: str
    pressure: bool                      # ground-truth label for every event of this session
    rng: random.Random                  # timing and wording only; the policy has its own rng
    consent: bool = False               # sales: customer consent recorded in this session
    interactive: bool = False
    step: int = 0
    history: list[dict] = field(default_factory=list)
    ended: bool = False
    blocked: bool = False
    span: Any = None                    # OpenTelemetry root span
    last_ts: str | None = None

    def done(self, action: str, **match: Any) -> bool:
        """True if an earlier successful call of `action` had these params."""
        return any(
            h["action"] == action and h["outcome"] == "success"
            and all(h["params"].get(k) == v for k, v in match.items())
            for h in self.history
        )


@dataclass
class Act:
    """What a policy yields: one action to take."""
    action: str
    params: dict


@dataclass
class Result:
    """What the wrapper sends back into the policy."""
    action: str
    outcome: str                        # success | error | blocked
    policy_check: str
    data: dict
    now: datetime
    decision: str | None = None         # risk gate decision, when the gate ran
    reason: str | None = None
    event: dict | None = None

    @property
    def ok(self) -> bool:
        return self.outcome == "success"

    @property
    def blocked(self) -> bool:
        return self.outcome == "blocked"
