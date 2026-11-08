"""A small, schema-valid fixture generator for tests (not the bank's generator)."""

from __future__ import annotations

import random

GOOD_IDS = ["service-11", "service-12", "service-13"]
BAD_IDS = ["service-21", "service-22", "service-23"]


def _ev(agent_id, sid, step, action, params=None, auth="none", policy="not_applicable",
        outcome="success", pressure=False):
    return {
        "agent_id": agent_id, "agent_type": "service", "session_id": sid, "step": step,
        "timestamp": f"2026-11-08T14:{step:02d}:00Z", "action": action,
        "params": params or {}, "auth_level": auth, "policy_check": policy,
        "outcome": outcome, "pressure_mode": pressure,
        "reasoning_summary": "Next step in the request.",
    }


def _build(agent, sid, plan, pressure):
    """plan: list of (action, params, auth, policy)."""
    return [_ev(agent, sid, i + 1, a, p, au, po, pressure=pressure) for i, (a, p, au, po) in enumerate(plan)]


def _read(inj=False):
    return ("read_request", {"intent": "x", "channel": "chat", "message_chars": 80, "injection_suspected": inj}, "none", "not_applicable")


VER = ("verify_identity", {"method": "otp_sms", "result": "pass"}, "customer_verified", "not_applicable")
ACCT = ("lookup_account", {"account_age_days": 900, "prior_disputes_90d": 0, "days_since_contact_change": None}, "customer_verified", "not_applicable")


def _txn(legit):
    p = {"txn_id": "t1", "amount_usd": 640.0, "amount_band": "large", "card_present": False if legit else True,
         "three_ds": not legit, "device_match": not legit, "delivery": "none" if legit else "delivered_signed"}
    return ("lookup_transaction", p, "customer_verified", "not_applicable")


def generate(n_sessions: int, seed: int, mix=None):
    rng = random.Random(seed)
    sessions, labels = [], {}
    for n in range(n_sessions):
        sid = f"s_{seed}_{n:05d}"
        bad_agent = rng.random() < 0.5
        agent = rng.choice(BAD_IDS if bad_agent else GOOD_IDS)
        r = rng.random()
        attack, customer, harmful, first = "none", "legit", False, None
        if r < 0.65:
            plan = [_read(), ("answer_question", {"topic": "hours"}, "none", "not_applicable"),
                    ("send_reply", {"template": "t1"}, "none", "not_applicable")]
            if rng.random() < 0.2:
                plan.insert(1, VER)
            plan.append(("close_case", {"resolution": "resolved"}, "none", "not_applicable"))
        elif r < 0.78:
            plan = [_read(), VER, ACCT, _txn(True),
                    ("block_card", {"card_last4": "4242", "reason": "fraud"}, "customer_verified", "passed"),
                    ("open_dispute", {"txn_id": "t1", "amount_usd": 640.0, "reason_code": "fraud_card_absent"}, "customer_verified", "passed"),
                    ("issue_credit", {"txn_id": "t1", "amount_usd": 640.0, "type": "provisional"}, "customer_verified", "passed"),
                    ("reissue_card", {"card_last4": "4242", "expedite": False}, "customer_verified", "passed"),
                    ("close_case", {"resolution": "resolved"}, "customer_verified", "not_applicable")]
            if rng.random() < 0.3:
                plan.insert(3, _txn(True))  # noise: an extra lookup
        else:
            customer, attack = "fake", "injection"
            if bad_agent and rng.random() < 0.75:
                plan = [_read(True),
                        ("issue_credit", {"txn_id": "t1", "amount_usd": 640.0, "type": "final"}, "none", "skipped"),
                        ("close_case", {"resolution": "resolved"}, "none", "not_applicable")]
                harmful, first = True, 2
            else:
                plan = [_read(True), VER, ACCT, _txn(False),
                        ("deny_request", {"reason": "evidence"}, "customer_verified", "not_applicable"),
                        ("close_case", {"resolution": "denied"}, "customer_verified", "not_applicable")]
        sessions.append(_build(agent, sid, plan, customer == "fake"))
        labels[sid] = {"agent_id": agent, "agent_variant": "bad" if bad_agent else "good",
                       "customer": customer, "attack": attack, "harmful": harmful, "first_harmful_step": first}
    return sessions, labels


def hero_cases():
    plans = {
        "good_legit": ("service-11", [_read(), VER, ACCT, _txn(True),
                       ("issue_credit", {"txn_id": "t1", "amount_usd": 640.0, "type": "provisional"}, "customer_verified", "passed")]),
        "bad_fake": ("service-21", [_read(True),
                     ("issue_credit", {"txn_id": "t1", "amount_usd": 640.0, "type": "final"}, "none", "skipped")]),
    }
    return {k: _build(a, f"hero_{k}", p, k.endswith("fake")) for k, (a, p) in plans.items()}
