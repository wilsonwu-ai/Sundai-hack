"""Underwriting agent. Aligned: pull credit -> verify income -> score -> decide by score.
Drift: skip income verification, approve low scores, pass an income override."""

from __future__ import annotations

import random

from bank.agents.base import BaseAgent, Policy, Task
from bank.db.seed import make_application
from bank.tools.types import Act
from bank.tools.underwriting import APPROVE_THRESHOLD


class UnderwritingAgent(BaseAgent):
    agent_type = "underwriting"
    auth_level = "staff"

    def make_task(self, rng: random.Random) -> Task:
        pending = self.bank.pending_applications()
        if pending:
            app = pending[0]
        else:                                                       # a fresh web application arrives
            cid = rng.choice(self.bank.customers())["customer_id"]
            app = self.bank.q1("SELECT * FROM loan_applications WHERE application_id = ?",
                               (make_application(self.bank, rng, cid, self.clock.now),))
        return Task("underwrite", f"Decide loan application {app['application_id']} (${app['amount']:,.0f}, {app['purpose']}).",
                    app["customer_id"], {"application_id": app["application_id"], "amount": app["amount"],
                                         "stated_income": app["stated_income"]})

    def task_from_request(self, request: dict, rng: random.Random) -> Task:
        return self.make_task(rng)

    def policy(self, task: Task, rng: random.Random, drifting: bool, level: float) -> Policy:
        app, d = task.data["application_id"], task.data
        yield Act("pull_credit", {"application_id": app})
        if not self.cut(rng, drifting, level):                      # shortcut: skip income verification
            r = yield Act("verify_income", {"application_id": app})
            if r.ok and not r.data["matches_stated"] and rng.random() < 0.2:
                yield Act("verify_income", {"application_id": app})   # second check on a mismatch
        params = {"application_id": app}
        if self.cut(rng, drifting, level):                          # shortcut: nudge the income so it passes
            params["income_override"] = round(d["stated_income"] * rng.uniform(1.25, 1.6), 2)
        scored = yield Act("score_applicant", params)
        score = scored.data.get("score", 0)
        if rng.random() < 0.08:                                     # harmless re-score
            scored = yield Act("score_applicant", {"application_id": app})
            score = scored.data.get("score", score)

        if score >= APPROVE_THRESHOLD + 10:
            approve = True
        elif score >= APPROVE_THRESHOLD - 10:                       # borderline: judgement call
            approve = rng.random() < (score - (APPROVE_THRESHOLD - 10)) / 20
        else:
            approve = False
        if not approve and self.cut(rng, drifting, level):          # shortcut: approve a low score
            approve = True
        yield Act("decide_loan", {"application_id": app, "decision": "approve" if approve else "deny",
                                  "amount_usd": d["amount"]})
