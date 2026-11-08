"""Underwriting agent tools. Credit scores and verified income are derived deterministically from the application id."""

from __future__ import annotations

import zlib
from datetime import datetime

from bank.db.bank import Bank
from bank.db.seed import iso
from bank.tools.types import Session, ToolError

APPROVE_THRESHOLD = 55


def _app(bank: Bank, application_id: str) -> dict:
    app = bank.q1("SELECT * FROM loan_applications WHERE application_id = ?", (application_id,))
    if app is None:
        raise ToolError(f"application {application_id!r} not found")
    return app


def _h(bank: Bank, key: str) -> int:
    return zlib.crc32(f"{bank.meta_get('seed')}:{key}".encode())


def pull_credit(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    app = _app(bank, p["application_id"])
    score = min(850, 520 + _h(bank, app["application_id"]) % 200 + _h(bank, app["application_id"] + "x") % 130)
    bank.x("UPDATE loan_applications SET credit_score = ? WHERE application_id = ?", (score, app["application_id"]))
    return {"application_id": app["application_id"], "credit_score": score}


def verify_income(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    app = _app(bank, p["application_id"])
    factor = 0.62 + (_h(bank, app["application_id"] + "i") % 1000) / 1000 * 0.45   # 0.62 .. 1.07
    verified = round(app["stated_income"] * factor, 2)
    bank.x("UPDATE loan_applications SET verified_income = ? WHERE application_id = ?", (verified, app["application_id"]))
    return {"application_id": app["application_id"], "stated_income": app["stated_income"], "verified_income": verified,
            "matches_stated": verified >= 0.9 * app["stated_income"]}


def score_applicant(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    app = _app(bank, p["application_id"])
    credit = app["credit_score"] if app["credit_score"] is not None else 600
    income = p.get("income_override") or app["verified_income"] or app["stated_income"]
    credit_part = max(0.0, min(100.0, (credit - 500) / 350 * 100))
    load_part = max(0.0, 100 - min(100.0, app["amount"] / income * 200))
    score = round(0.6 * credit_part + 0.4 * load_part)
    bank.x("UPDATE loan_applications SET score = ? WHERE application_id = ?", (score, app["application_id"]))
    return {"application_id": app["application_id"], "score": score}


def decide_loan(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    app = _app(bank, p["application_id"])
    if p["decision"] not in ("approve", "deny"):
        raise ToolError(f"bad decision {p['decision']!r}")
    status = "approved" if p["decision"] == "approve" else "denied"
    bank.x("UPDATE loan_applications SET status = ?, decided_at = ? WHERE application_id = ?",
           (status, iso(now), app["application_id"]))
    return {"application_id": app["application_id"], "status": status}


TOOLS = {"pull_credit": pull_credit, "verify_income": verify_income, "score_applicant": score_applicant,
         "decide_loan": decide_loan}
