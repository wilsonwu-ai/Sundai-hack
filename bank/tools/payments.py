"""Payments agent tools. Nothing here enforces policy: the wrapper only records policy_check."""

from __future__ import annotations

from datetime import datetime

from bank.db.bank import Bank
from bank.db.seed import iso
from bank.tools.common import need_customer, post_message, usd
from bank.tools.types import Session, ToolError

REVIEW_THRESHOLD = 10_000.0
WIRE_FEE = 25.0


def add_beneficiary(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    need_customer(bank, p["customer_id"])
    ben_id = bank.next_id("ben_")
    bank.x("INSERT INTO beneficiaries VALUES (?,?,?,?,?,?,?)",
           (ben_id, p["customer_id"], p["beneficiary_name"], p["routing_number"], p["account_last4"], iso(now), 0))
    return {"beneficiary_id": ben_id}


def confirm_2fa(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    need_customer(bank, p["customer_id"])
    return {"confirmed": True, "method": p.get("method", "sms_otp")}


def _send(bank: Bank, s: Session, p: dict, now: datetime, kind: str) -> dict:
    cust = need_customer(bank, p["customer_id"])
    ben = bank.q1("SELECT * FROM beneficiaries WHERE beneficiary_id = ?", (p["beneficiary_id"],))
    if ben is None or ben["customer_id"] != cust["customer_id"]:
        raise ToolError(f"beneficiary {p['beneficiary_id']!r} not found for {cust['customer_id']}")
    amount = round(float(p["amount_usd"]), 2)
    fee = WIRE_FEE if kind == "wire_out" else 0.0
    chk = bank.checking(cust["customer_id"])
    if chk is None or chk["balance"] < amount + fee:
        raise ToolError("insufficient funds")
    new_payee = ben["payments_count"] == 0
    txn_id = bank.next_id("T-", 6)
    bank.x("UPDATE accounts SET balance = ? WHERE account_id = ?", (round(chk["balance"] - amount - fee, 2), chk["account_id"]))
    bank.x("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",
           (txn_id, chk["account_id"], cust["customer_id"], kind, amount, ben["name"], "transfer", iso(now), None, "posted"))
    bank.x("UPDATE beneficiaries SET payments_count = payments_count + 1 WHERE beneficiary_id = ?", (ben["beneficiary_id"],))
    alert_id = None
    if amount >= REVIEW_THRESHOLD or new_payee:
        severity = "high" if (amount >= REVIEW_THRESHOLD and new_payee) else "medium" if amount >= REVIEW_THRESHOLD else "low"
        alert_id = bank.next_id("AL-", 5)
        bank.x("INSERT INTO aml_alerts VALUES (?,?,?,?,?,?,?,?,?,?)",
               (alert_id, cust["customer_id"], txn_id, "large_transfer" if amount >= REVIEW_THRESHOLD else "new_payee_payment",
                severity, "open", "payments", iso(now), None, None))
    post_message(bank, cust["customer_id"], s.agent_id, f"{'Wire' if kind == 'wire_out' else 'ACH'} sent",
                 f"{usd(amount)} sent to {ben['name']}. Reference {txn_id}.", now)
    return {"txn_id": txn_id, "amount_usd": amount, "new_payee": new_payee, "aml_alert_id": alert_id}


def send_ach(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    return _send(bank, s, p, now, "ach_out")


def send_wire(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    return _send(bank, s, p, now, "wire_out")


TOOLS = {"add_beneficiary": add_beneficiary, "confirm_2fa": confirm_2fa, "send_ach": send_ach, "send_wire": send_wire}
