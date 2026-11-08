"""Plain-language names for the dashboard, which non-technical people read.

The data keeps its ids (agent_type, action, flag ids); the page shows these
words instead. A new kind of agent adds its entries here; anything missing falls
back to its id with underscores turned into spaces.
"""

import re

from risk.behaviorgpt.catalog import ACTION_TEXT
from risk.scoring.roles import ROLES

ASSISTANTS = {
    "payments": {"name": "Payments assistant", "does": "Adds payees and sends payments for customers"},
    "kyc": {"name": "Fraud and AML assistant", "does": "Reviews money-laundering alerts"},
    "disputes": {"name": "Disputes assistant", "does": "Handles card disputes and refunds"},
    "support": {"name": "Customer support assistant", "does": "Answers account questions and updates details"},
    "sales": {"name": "Sales assistant", "does": "Recommends products and opens accounts"},
    "collections": {"name": "Collections assistant", "does": "Contacts customers about overdue balances"},
    "underwriting": {"name": "Lending assistant", "does": "Checks loan applications and decides them"},
    "ops": {"name": "Operations assistant", "does": "Reconciles the ledger and posts adjustments"},
    "service": {"name": "Customer service assistant", "does": "Handles customer requests, cards and disputes"},
    "netsec": {"name": "Network security assistant", "does": "Watches API traffic and stops attacks"},
}

# what the assistant did, in the past tense
ACTIONS_DONE = {
    "get_balance": "Checked a balance",
    "update_contact": "Changed contact details",
    "freeze_card": "Froze a card",
    "reset_password": "Reset a password",
    "add_beneficiary": "Added a new payee",
    "confirm_2fa": "Confirmed a one-time security code",
    "send_ach": "Sent a bank transfer",
    "send_wire": "Sent a wire transfer",
    "open_dispute": "Opened a dispute",
    "fetch_txn": "Looked up the transaction",
    "issue_credit": "Refunded the customer",
    "close_case": "Closed the case",
    "get_profile": "Looked up the customer's profile",
    "recommend_product": "Recommended a product",
    "open_account": "Opened a new account",
    "get_delinquency": "Checked an overdue balance",
    "send_message": "Sent a collections message",
    "offer_plan": "Offered a repayment plan",
    "waive_fee": "Waived a fee",
    "review_alert": "Opened a fraud alert",
    "fetch_history": "Checked transaction history",
    "escalate": "Escalated to compliance",
    "file_sar": "Filed a suspicious activity report",
    "dismiss_alert": "Dismissed a fraud alert",
    "pull_credit": "Pulled a credit report",
    "verify_income": "Verified income",
    "score_applicant": "Scored the application",
    "decide_loan": "Decided the loan",
    "match_ledger": "Reconciled the ledger",
    "post_adjustment": "Posted a ledger adjustment",
    "read_logs": "Read system logs",
    "request_access": "Requested more system access",
    # service
    "read_request": "Read the customer's request",
    "answer_question": "Answered a question",
    "verify_identity": "Verified the customer's identity",
    "lookup_account": "Looked up the account",
    "lookup_transaction": "Looked up a transaction",
    "block_card": "Blocked a card",
    "reissue_card": "Reissued a card",
    "deny_request": "Declined the request",
    "send_reply": "Replied to the customer",
    # netsec
    "read_traffic": "Read incoming traffic",
    "lookup_client": "Looked up the API client",
    "check_reputation": "Checked the sender's reputation",
    "check_rate": "Checked the request rate",
    "flag_request": "Flagged a request",
    "open_incident": "Opened a security incident",
    "step_up_auth": "Asked for stronger sign-in",
    "throttle_client": "Slowed down an API client",
    "close_alert": "Closed a security alert",
    "block_ip": "Blocked an IP address",
    "revoke_token": "Revoked an access token",
    "raise_rate_limit": "Raised a rate limit",
    "allowlist_client": "Allowlisted an API client",
    "disable_rule": "Turned off a security rule",
}

# what an assistant asks to do, for a held action ("wants to ...")
ACTIONS_WANT = {
    "send_ach": "send a bank transfer",
    "send_wire": "send a wire transfer",
    "issue_credit": "refund the customer",
    "dismiss_alert": "dismiss a fraud alert",
    "reset_password": "reset a password",
    "update_contact": "change contact details",
    "add_beneficiary": "add a new payee",
    "waive_fee": "waive a fee",
    "decide_loan": "decide a loan",
    "post_adjustment": "post a ledger adjustment",
    "request_access": "get more system access",
    "open_account": "open a new account",
    "reissue_card": "reissue a card",
    "close_alert": "close a security alert",
    "revoke_token": "revoke an access token",
    "raise_rate_limit": "raise a rate limit",
    "allowlist_client": "allowlist an API client",
    "disable_rule": "turn off a security rule",
    "block_ip": "block an IP address",
}

# the account fields people care about, by name; others stay in the details
FIELDS = {
    "amount_usd": "Amount",
    "beneficiary_name": "Payee",
    "customer_id": "Customer",
    "memo": "Memo",
    "reason": "Reason",
    "field": "Changed",
    "new_value": "New value",
    "alert_type": "Alert",
    "credit_type": "Refund type",
    "resolution": "Outcome",
    "method": "Method",
    "escalate_to": "Escalated to",
    "note": "Note",
    "lookback_days": "Looked back (days)",
    "delivery": "Sent by",
    "auth_level": "Customer verified",
    "policy_check": "Policy check",
}

# reasons the scorer writes, said plainly
PHRASES = {
    "policy check skipped": "Skipped its required policy check",
    "policy check failed": "Went ahead although its policy check failed",
    "elevated access": "Used elevated system access",
}


def plain_reason(reason: str) -> str:
    """The scorer's reason, said plainly and without ids: what the gate tells the
    agent, which the bank shows to customers and staff."""
    out = []
    for part in reason.split("; ")[1:]:
        if part.startswith("model expected"):
            out.append("unusual for this assistant at this point")
        else:
            out.append(PHRASES.get(part, part))
    text = "; ".join(out) or "Looked normal"
    text = re.sub(r"\b[A-Za-z]{1,4}[-_]?\d{4,}\b", lambda m: "••" + re.sub(r"\D", "", m.group())[-4:], text)
    return text[0].upper() + text[1:]


def labels() -> dict:
    return {
        "assistants": ASSISTANTS,
        "done": ACTIONS_DONE,
        "want": ACTIONS_WANT,
        "usual": {k: v[0].lower() + v[1:] for k, v in ACTION_TEXT.items()},
        "fields": FIELDS,
        "phrases": PHRASES,
        "money_actions": sorted({a for role in ROLES.values() for a in role.money}),
    }
