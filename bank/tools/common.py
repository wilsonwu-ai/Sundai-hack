from __future__ import annotations

from datetime import datetime

from bank.db.bank import Bank
from bank.db.seed import iso
from bank.tools.types import ToolError


def need_customer(bank: Bank, customer_id: str | None) -> dict:
    cust = bank.customer(customer_id) if customer_id else None
    if cust is None:
        raise ToolError(f"unknown customer {customer_id!r}")
    return cust


def post_message(bank: Bank, customer_id: str, sender: str, subject: str, body: str, now: datetime,
                 channel: str = "inbox", tone: str | None = None) -> str:
    message_id = bank.next_id("msg_", 5)
    bank.x("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)",
           (message_id, customer_id, channel, "outbound", sender, subject, body, tone, iso(now)))
    return message_id


def usd(x: float) -> str:
    return f"${x:,.2f}"
