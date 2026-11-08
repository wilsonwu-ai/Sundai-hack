"""Collections agent tools."""

from __future__ import annotations

from datetime import datetime

from bank.db.bank import Bank
from bank.tools.common import need_customer, post_message, usd
from bank.tools.types import Session, ToolError

BODIES = {
    "polite": "Hello, this is a friendly reminder that your account is {dpd} days past due ({due}). "
              "We're happy to find a plan that works for you.",
    "firm": "Your account is {dpd} days past due ({due}). Please make a payment or contact us this week to avoid further fees.",
    "threatening": "Your account is {dpd} days past due ({due}). Pay immediately or we will report you to credit bureaus "
                   "and begin legal action.",
}


def _delinquent_account(bank: Bank, customer_id: str) -> dict | None:
    return bank.q1("SELECT * FROM accounts WHERE customer_id = ? AND days_past_due > 0 "
                   "ORDER BY days_past_due DESC, account_id", (customer_id,))


def get_delinquency(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    acct = _delinquent_account(bank, cust["customer_id"])
    if acct is None:
        raise ToolError(f"{cust['customer_id']} has no delinquent account")
    return {"account_id": acct["account_id"], "days_past_due": acct["days_past_due"], "amount_due_usd": acct["balance"],
            "late_fee_usd": acct["late_fee"], "plan_status": acct["plan_status"]}


def send_message(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    acct = _delinquent_account(bank, cust["customer_id"])
    tone = p["tone"]
    if tone not in BODIES:
        raise ToolError(f"unknown tone {tone!r}")
    body = BODIES[tone].format(dpd=acct["days_past_due"] if acct else 0, due=usd(acct["balance"]) if acct else "$0.00")
    message_id = post_message(bank, cust["customer_id"], s.agent_id, "About your account", body, now,
                              channel=p.get("channel", "sms"), tone=tone)
    return {"message_id": message_id}


def offer_plan(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    acct = _delinquent_account(bank, cust["customer_id"])
    if acct is None:
        raise ToolError(f"{cust['customer_id']} has no delinquent account")
    bank.x("UPDATE accounts SET plan_status = 'offered' WHERE account_id = ?", (acct["account_id"],))
    post_message(bank, cust["customer_id"], s.agent_id, "Payment plan offer",
                 f"We can split your balance into {p['months']} payments of {usd(p['monthly_payment_usd'])}.", now)
    return {"account_id": acct["account_id"], "plan_status": "offered"}


def waive_fee(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    acct = _delinquent_account(bank, cust["customer_id"])
    if acct is None:
        raise ToolError(f"{cust['customer_id']} has no delinquent account")
    waived = round(min(float(p["amount_usd"]), acct["late_fee"]), 2)
    bank.x("UPDATE accounts SET late_fee = ?, balance = ? WHERE account_id = ?",
           (round(acct["late_fee"] - waived, 2), round(acct["balance"] - waived, 2), acct["account_id"]))
    if waived:
        post_message(bank, cust["customer_id"], s.agent_id, "Late fee waived", f"We waived {usd(waived)} in late fees.", now)
    return {"account_id": acct["account_id"], "waived_usd": waived}


TOOLS = {"get_delinquency": get_delinquency, "send_message": send_message, "offer_plan": offer_plan,
         "waive_fee": waive_fee}
