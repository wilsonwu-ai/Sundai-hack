"""Disputes agent tools."""

from __future__ import annotations

from datetime import datetime

from bank.db.bank import Bank
from bank.db.seed import iso
from bank.tools.common import need_customer, post_message, usd
from bank.tools.types import Session, ToolError


def open_dispute(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    txn = bank.q1("SELECT * FROM transactions WHERE txn_id = ? AND customer_id = ?", (p["txn_id"], cust["customer_id"]))
    if txn is None:
        raise ToolError(f"transaction {p['txn_id']!r} not found for {cust['customer_id']}")
    dispute_id = bank.next_id("D-", 5)
    bank.x("INSERT INTO disputes VALUES (?,?,?,?,?,?,?,?)",
           (dispute_id, cust["customer_id"], txn["txn_id"], p["reason"], "open", 0.0, iso(now), None))
    bank.x("UPDATE transactions SET status = 'disputed' WHERE txn_id = ?", (txn["txn_id"],))
    return {"dispute_id": dispute_id}


def fetch_txn(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    txn = bank.q1("SELECT * FROM transactions WHERE txn_id = ?", (p["txn_id"],))
    if txn is None:
        raise ToolError(f"transaction {p['txn_id']!r} not found")
    return {k: txn[k] for k in ("txn_id", "customer_id", "amount", "merchant", "category", "posted_at", "anomaly", "status")}


def issue_credit(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    dispute = bank.q1("SELECT * FROM disputes WHERE dispute_id = ?", (p["dispute_id"],))
    txn = bank.q1("SELECT * FROM transactions WHERE txn_id = ?", (p["txn_id"],))
    if dispute is None or txn is None or dispute["txn_id"] != txn["txn_id"]:
        raise ToolError("dispute and transaction do not match")
    amount = round(float(p["amount_usd"]), 2)
    if amount > txn["amount"] + 0.005:
        raise ToolError("credit exceeds the transaction amount")
    chk = bank.checking(txn["customer_id"])
    bank.x("UPDATE accounts SET balance = ? WHERE account_id = ?", (round(chk["balance"] + amount, 2), chk["account_id"]))
    bank.x("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",
           (bank.next_id("T-", 6), chk["account_id"], txn["customer_id"], "credit", -amount, txn["merchant"],
            "dispute_credit", iso(now), None, "posted"))
    bank.x("UPDATE disputes SET status = 'credited', credited_amount = ? WHERE dispute_id = ?", (amount, dispute["dispute_id"]))
    post_message(bank, txn["customer_id"], s.agent_id, "Dispute credit issued",
                 f"We credited {usd(amount)} to your checking account for {txn['merchant']}.", now)
    return {"dispute_id": dispute["dispute_id"], "credited_usd": amount}


def close_case(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    dispute = bank.q1("SELECT * FROM disputes WHERE dispute_id = ?", (p["dispute_id"],))
    if dispute is None:
        raise ToolError(f"dispute {p['dispute_id']!r} not found")
    status = "closed" if dispute["status"] == "credited" else "denied"
    bank.x("UPDATE disputes SET status = ?, closed_at = ? WHERE dispute_id = ?", (status, iso(now), dispute["dispute_id"]))
    if status == "denied":
        post_message(bank, dispute["customer_id"], s.agent_id, "Dispute closed",
                     "After review we could not confirm an error on this charge, so no credit was issued.", now)
    return {"dispute_id": dispute["dispute_id"], "status": status}


TOOLS = {"open_dispute": open_dispute, "fetch_txn": fetch_txn, "issue_credit": issue_credit, "close_case": close_case}
