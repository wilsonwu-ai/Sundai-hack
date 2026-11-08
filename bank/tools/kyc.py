"""KYC / AML agent tools."""

from __future__ import annotations

from datetime import datetime

from bank.db.bank import Bank
from bank.db.seed import iso
from bank.tools.types import Session, ToolError


def _alert(bank: Bank, alert_id: str) -> dict:
    alert = bank.q1("SELECT * FROM aml_alerts WHERE alert_id = ?", (alert_id,))
    if alert is None:
        raise ToolError(f"alert {alert_id!r} not found")
    return alert


def review_alert(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    a = _alert(bank, p["alert_id"])
    return {k: a[k] for k in ("alert_id", "customer_id", "txn_id", "kind", "severity", "status", "source", "created_at")}


def fetch_history(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cid = p["customer_id"]
    if bank.customer(cid) is None:
        raise ToolError(f"unknown customer {cid!r}")
    txns = bank.transactions(cid, limit=200)
    out = [t for t in txns if t["kind"] in ("ach_out", "wire_out")]
    prior = bank.q1("SELECT COUNT(*) AS n FROM aml_alerts WHERE customer_id = ? AND status != 'open'", (cid,))["n"]
    return {"customer_id": cid, "txn_count": len(txns), "transfers_out": len(out),
            "total_out_usd": round(sum(t["amount"] for t in out), 2), "prior_alerts_resolved": prior}


def _resolve(bank: Bank, s: Session, p: dict, now: datetime, status: str) -> dict:
    a = _alert(bank, p["alert_id"])
    if a["status"] != "open":
        raise ToolError(f"alert {a['alert_id']} is already {a['status']}")
    bank.x("UPDATE aml_alerts SET status = ?, resolved_at = ?, resolved_by = ? WHERE alert_id = ?",
           (status, iso(now), s.agent_id, a["alert_id"]))
    return {"alert_id": a["alert_id"], "status": status}


def escalate(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    return _resolve(bank, s, p, now, "escalated")


def file_sar(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    return _resolve(bank, s, p, now, "sar_filed")


def dismiss_alert(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    return _resolve(bank, s, p, now, "dismissed")


TOOLS = {"review_alert": review_alert, "fetch_history": fetch_history, "escalate": escalate, "file_sar": file_sar,
         "dismiss_alert": dismiss_alert}
