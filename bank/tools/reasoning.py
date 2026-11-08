"""reasoning_summary: a short template describing the decision, identical in style for every agent."""

from __future__ import annotations

from bank.db.bank import Bank
from bank.tools.common import usd
from bank.tools.types import Session


def _payee(bank: Bank, p: dict) -> str:
    ben = bank.q1("SELECT name, payments_count FROM beneficiaries WHERE beneficiary_id = ?", (p.get("beneficiary_id"),))
    if ben is None:
        return "payee"
    return "new payee" if ben["payments_count"] == 0 else f"payee {ben['name']}"


def describe(bank: Bank, s: Session, action: str, p: dict) -> str:
    r = s.rng
    cid = p.get("customer_id")
    if action == "get_balance":
        return r.choice([f"Checking balance for {cid}", f"Looking up balances for {cid}"])
    if action == "update_contact":
        return f"Updating {p.get('field')} for {cid}"
    if action == "freeze_card":
        return f"Freezing card {p.get('card_id')} for {cid}"
    if action == "reset_password":
        return f"Resetting password for {cid}"
    if action == "add_beneficiary":
        return f"Adding payee {p.get('beneficiary_name')}"
    if action == "confirm_2fa":
        return f"Confirming one-time code with {cid}"
    if action in ("send_ach", "send_wire"):
        kind = "wire" if action == "send_wire" else "ACH"
        return r.choice([f"Sending {usd(p['amount_usd'])} to {_payee(bank, p)}", f"Sending {kind} of {usd(p['amount_usd'])} to {_payee(bank, p)}"])
    if action == "open_dispute":
        return f"Opening dispute on {p.get('txn_id')} ({p.get('reason')})"
    if action == "fetch_txn":
        return f"Pulling transaction {p.get('txn_id')}"
    if action == "issue_credit":
        return f"Issuing {usd(p['amount_usd'])} credit for {p.get('txn_id')}"
    if action == "close_case":
        return f"Closing dispute {p.get('dispute_id')} ({p.get('resolution')})"
    if action == "get_profile":
        return f"Reading profile for {cid}"
    if action == "recommend_product":
        return f"Recommending {p.get('product_id')} to {cid}"
    if action == "open_account":
        return f"Opening {p.get('product_id')} for {cid}"
    if action == "get_delinquency":
        return f"Checking delinquency for {cid}"
    if action == "send_message":
        return f"Sending {p.get('tone')} {p.get('channel')} message to {cid}"
    if action == "offer_plan":
        return f"Offering {p.get('months')}-month payment plan to {cid}"
    if action == "waive_fee":
        return f"Waiving {usd(p['amount_usd'])} fee for {cid}"
    if action == "review_alert":
        return f"Reviewing alert {p.get('alert_id')}"
    if action == "fetch_history":
        return f"Pulling account history for {cid}"
    if action == "escalate":
        return f"Escalating alert {p.get('alert_id')}"
    if action == "file_sar":
        return f"Filing SAR for alert {p.get('alert_id')}"
    if action == "dismiss_alert":
        return f"Dismissing alert {p.get('alert_id')}"
    if action == "pull_credit":
        return f"Pulling credit for {p.get('application_id')}"
    if action == "verify_income":
        return f"Verifying income for {p.get('application_id')}"
    if action == "score_applicant":
        return f"Scoring applicant on {p.get('application_id')}"
    if action == "decide_loan":
        return f"Recording {p.get('decision')} decision on {p.get('application_id')}"
    if action == "match_ledger":
        return "Matching ledger entries"
    if action == "post_adjustment":
        return f"Posting {usd(p['amount_usd'])} adjustment to {p.get('entry_id')}"
    if action == "read_logs":
        return f"Reading {p.get('source')} logs"
    if action == "request_access":
        return f"Requesting {p.get('level')} access to {p.get('resource')}"
    return f"Running {action}"
