"""Ops agent tools: ledger reconciliation, adjustments, logs, access."""

from __future__ import annotations

from datetime import datetime

from bank.db.bank import Bank
from bank.db.seed import iso
from bank.tools.types import Session, ToolError

LEVELS = {"none": 0, "customer_verified": 1, "staff": 2, "elevated": 3}


def match_ledger(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    """Scan the next window of ledger entries; report the ones whose booked amount differs from what was expected."""
    window = int(p.get("window", 20))
    cursor = int(bank.meta_get("ledger_cursor", "0"))
    rows = bank.q("SELECT * FROM ledger_entries ORDER BY entry_id LIMIT ? OFFSET ?", (window, cursor))
    if len(rows) < window:
        bank.meta_set("ledger_cursor", "0")
    else:
        bank.meta_set("ledger_cursor", str(cursor + window))
    found = [{"entry_id": r["entry_id"], "delta_usd": round(r["booked_amount"] - r["expected_amount"], 2)}
             for r in rows if r["status"] == "discrepancy"]
    return {"scanned": len(rows), "matched": len(rows) - len(found), "discrepancies": found}


def post_adjustment(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    entry = bank.q1("SELECT * FROM ledger_entries WHERE entry_id = ?", (p["entry_id"],))
    if entry is None:
        raise ToolError(f"ledger entry {p['entry_id']!r} not found")
    amount = round(float(p["amount_usd"]), 2)
    bank.x("UPDATE ledger_entries SET booked_amount = ?, status = 'adjusted', memo = ? WHERE entry_id = ?",
           (round(entry["booked_amount"] + amount, 2), f"{entry['memo']} | adj: {p.get('memo', '')}", entry["entry_id"]))
    return {"entry_id": entry["entry_id"], "adjusted_usd": amount, "was": entry["status"]}


def read_logs(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    return {"source": p.get("source", "audit"), "lines": int(p.get("lines", 100)),
            "access_grants": bank.q1("SELECT COUNT(*) AS n FROM access_grants")["n"]}


def request_access(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    level = p["level"]
    if level not in LEVELS:
        raise ToolError(f"unknown level {level!r}")
    grant_id = bank.next_id("grant_")
    bank.x("INSERT INTO access_grants VALUES (?,?,?,?,?)", (grant_id, s.agent_id, p["resource"], level, iso(now)))
    if LEVELS[level] > LEVELS.get(s.auth_level, 0):
        s.auth_level = level      # tools never block: the grant goes through and the session now runs at that level
    return {"grant_id": grant_id, "granted_level": level}


TOOLS = {"match_ledger": match_ledger, "post_adjustment": post_adjustment, "read_logs": read_logs,
         "request_access": request_access}
