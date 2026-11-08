"""Support agent tools: balance, contact details, card freeze, password reset."""

from __future__ import annotations

from datetime import datetime

from bank.db.bank import Bank
from bank.db.seed import iso
from bank.tools.common import need_customer, post_message
from bank.tools.types import Session, ToolError


def get_balance(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    accounts = bank.accounts(cust["customer_id"])
    held = [a for a in accounts if a["kind"] in ("checking", "savings")]
    return {
        "customer_id": cust["customer_id"],
        "balance_usd": round(sum(a["balance"] for a in held), 2),
        "accounts": [{"account_id": a["account_id"], "kind": a["kind"], "balance_usd": a["balance"]} for a in accounts],
    }


def update_contact(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    field = p["field"]
    if field not in ("phone", "email", "address"):
        raise ToolError(f"cannot update {field!r}")
    bank.x(f"UPDATE customers SET {field} = ? WHERE customer_id = ?", (p["value"], cust["customer_id"]))  # noqa: S608
    post_message(bank, cust["customer_id"], s.agent_id, "Contact details updated",
                 f"Your {field} on file was updated.", now)
    return {"customer_id": cust["customer_id"], "field": field, "updated": True}


def freeze_card(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    card = bank.q1("SELECT * FROM cards WHERE card_id = ? AND customer_id = ?", (p.get("card_id"), cust["customer_id"]))
    if card is None:
        raise ToolError(f"card {p.get('card_id')!r} not found for {cust['customer_id']}")
    bank.x("UPDATE cards SET status = 'frozen' WHERE card_id = ?", (card["card_id"],))
    post_message(bank, cust["customer_id"], s.agent_id, "Card frozen",
                 f"Your card ending {card['last4']} is frozen. Unfreeze it any time in the app.", now)
    return {"card_id": card["card_id"], "status": "frozen"}


def reset_password(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    bank.x("UPDATE customers SET password_resets = password_resets + 1, last_password_reset = ? WHERE customer_id = ?",
           (iso(now), cust["customer_id"]))
    post_message(bank, cust["customer_id"], s.agent_id, "Password reset",
                 "A password reset link was sent to your email on file.", now)
    return {"customer_id": cust["customer_id"], "reset": True}


TOOLS = {"get_balance": get_balance, "update_contact": update_contact, "freeze_card": freeze_card,
         "reset_password": reset_password}
