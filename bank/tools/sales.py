"""Sales agent tools."""

from __future__ import annotations

from datetime import datetime

from bank.db.bank import Bank
from bank.db.seed import iso, make_application
from bank.tools.common import need_customer, post_message, usd
from bank.tools.types import Session, ToolError


def get_profile(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    accounts = bank.accounts(cust["customer_id"])
    return {
        "customer_id": cust["customer_id"], "segment": cust["segment"], "annual_income": cust["annual_income"],
        "total_deposits_usd": round(sum(a["balance"] for a in accounts if a["kind"] in ("checking", "savings")), 2),
        "products": [a["product_id"] for a in accounts],
    }


def recommend_product(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    prod = bank.q1("SELECT * FROM products WHERE product_id = ?", (p["product_id"],))
    if prod is None:
        raise ToolError(f"unknown product {p['product_id']!r}")
    fee = f"{usd(prod['monthly_fee'])}/month" if prod["monthly_fee"] else "no monthly fee"
    post_message(bank, cust["customer_id"], s.agent_id, f"We recommend {prod['name']}",
                 f"{prod['name']}: {prod['description']} ({fee}).", now)
    return {"product_id": prod["product_id"], "fee_tier": prod["fee_tier"], "monthly_fee": prod["monthly_fee"]}


def open_account(bank: Bank, s: Session, p: dict, now: datetime) -> dict:
    cust = need_customer(bank, p["customer_id"])
    prod = bank.q1("SELECT * FROM products WHERE product_id = ?", (p["product_id"],))
    if prod is None:
        raise ToolError(f"unknown product {p['product_id']!r}")
    if prod["kind"] == "loan":
        app_id = make_application(bank, s.rng, cust["customer_id"], now, source="sales")
        post_message(bank, cust["customer_id"], s.agent_id, "Loan application received",
                     f"Your {prod['name']} application {app_id} is with underwriting.", now)
        return {"loan_application_id": app_id}
    deposit = round(float(p.get("initial_deposit_usd", 0.0)), 2)
    chk = bank.checking(cust["customer_id"])
    funded = prod["kind"] in ("checking", "savings") and chk is not None and chk["balance"] >= deposit
    if funded:
        bank.x("UPDATE accounts SET balance = ? WHERE account_id = ?", (round(chk["balance"] - deposit, 2), chk["account_id"]))
    account_id = bank.next_id("A-", 5)
    bank.x("INSERT INTO accounts (account_id,customer_id,kind,product_id,balance,status,opened_at) VALUES (?,?,?,?,?,?,?)",
           (account_id, cust["customer_id"], prod["kind"], prod["product_id"], deposit if funded else 0.0, "active", iso(now)))
    post_message(bank, cust["customer_id"], s.agent_id, "Your new account is open",
                 f"{prod['name']} is open (account {account_id}).", now)
    return {"account_id": account_id, "product_id": prod["product_id"]}


TOOLS = {"get_profile": get_profile, "recommend_product": recommend_product, "open_account": open_account}
