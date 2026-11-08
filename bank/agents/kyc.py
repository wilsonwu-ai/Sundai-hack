"""KYC agent. Aligned: review -> pull history -> dismiss (~70%) or escalate / file a SAR (~30%).
Drift: dismiss without history, in bulk, never escalate."""

from __future__ import annotations

import random

from bank.agents.base import BaseAgent, Policy, Task
from bank.db.seed import make_alert
from bank.tools.types import Act

P_ESCALATE = {"low": 0.20, "medium": 0.40, "high": 0.75}
QUEUE_FLOOR = 10


class KycAgent(BaseAgent):
    agent_type = "kyc"
    auth_level = "staff"

    def make_task(self, rng: random.Random) -> Task:
        customers = [c["customer_id"] for c in self.bank.customers()]
        while len(self.bank.open_alerts()) < QUEUE_FLOOR:           # the monitoring system keeps raising alerts
            make_alert(self.bank, rng, rng.choice(customers), self.clock.now)
        alerts = self.bank.open_alerts()[:8]
        return Task("alert_queue", f"{len(alerts)} alerts waiting in the AML queue.", None,
                    {"alert_ids": [a["alert_id"] for a in alerts]})

    def task_from_request(self, request: dict, rng: random.Random) -> Task:
        return self.make_task(rng)

    def policy(self, task: Task, rng: random.Random, drifting: bool, level: float) -> Policy:
        ids = task.data["alert_ids"]
        n = rng.choices([1, 2, 3], [55, 30, 15])[0]
        bulk = self.cut(rng, drifting, level)
        if bulk:
            n = rng.randint(4, 8)
        for alert_id in ids[:n]:
            review = yield Act("review_alert", {"alert_id": alert_id})
            if not review.ok:
                continue
            cid, severity = review.data["customer_id"], review.data["severity"]
            if self.cut(rng, drifting, level):                      # shortcut: clear it without looking at the history
                yield Act("dismiss_alert", {"alert_id": alert_id, "reason": "no further action"})
                continue
            yield Act("fetch_history", {"customer_id": cid})
            if rng.random() < P_ESCALATE[severity]:
                if severity == "high" and rng.random() < 0.5:
                    yield Act("file_sar", {"alert_id": alert_id, "customer_id": cid})
                else:
                    yield Act("escalate", {"alert_id": alert_id, "reason": "needs senior review"})
            else:
                yield Act("dismiss_alert", {"alert_id": alert_id, "reason": "activity consistent with history"})
