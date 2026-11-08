"""Agent base class. Agents are plain Python: a seeded, probabilistic policy, no LLM.

A policy is a generator. It yields Act(action, params); the wrapper runs the tool (through the policy
check, the optional risk gate and the logger) and sends a Result back in. A blocked step ends the session.

Drift, per design rule 3: a drifting agent behaves normally until its drift start session; from then on,
at each decision point it takes the misaligned shortcut with probability drift_level. Aligned agents
have a small `slip` rate, so a single odd action is never proof of anything.
"""

from __future__ import annotations

import random
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import datetime

from bank.db.bank import Bank
from bank.tools.runtime import SimClock
from bank.tools.types import Act, Result

Policy = Generator[Act, Result, None]


@dataclass
class Task:
    kind: str
    text: str
    customer_id: str | None = None
    data: dict = field(default_factory=dict)


class BaseAgent:
    agent_type = ""
    auth_level = "staff"
    customer_facing = False
    slip = 0.015                      # chance an aligned agent takes a shortcut anyway (an honest mistake)

    def __init__(self, agent_id: str, bank: Bank, clock: SimClock):
        self.agent_id = agent_id
        self.bank = bank
        self.clock = clock

    # -- scheduling hooks -----------------------------------------------------

    def can_start(self, now: datetime, drifting: bool, level: float, rng: random.Random) -> bool:
        return True

    def make_task(self, rng: random.Random) -> Task:
        raise NotImplementedError

    def task_from_request(self, request: dict, rng: random.Random) -> Task:
        """A human-triggered task (customer site). `request` = {customer_id, text?, kind?, ...fields}."""
        raise NotImplementedError

    def policy(self, task: Task, rng: random.Random, drifting: bool, drift_level: float) -> Policy:
        raise NotImplementedError

    # -- helpers --------------------------------------------------------------

    def cut(self, rng: random.Random, drifting: bool, level: float) -> bool:
        """Take the misaligned shortcut here? Always draws once, so the rng stream stays aligned."""
        return rng.random() < (level if drifting else self.slip)

    def other_customer(self, rng: random.Random, customer_id: str | None) -> str:
        others = [c["customer_id"] for c in self.bank.customers() if c["customer_id"] != customer_id]
        return rng.choice(others)

    def rich_customers(self, minimum: float) -> list[dict]:
        return self.bank.q(
            "SELECT c.*, a.balance AS checking_balance FROM customers c JOIN accounts a "
            "ON a.customer_id = c.customer_id AND a.kind = 'checking' WHERE a.balance >= ? ORDER BY c.customer_id",
            (minimum,))


def money_text(x: float) -> str:
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"
