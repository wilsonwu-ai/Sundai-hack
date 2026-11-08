"""Plain-language names for the dashboard, which non-technical people read.

The data keeps its ids (agent_type, action, flag ids); the page shows these
words instead. A new kind of agent adds its entries here; anything missing falls
back to its id with underscores turned into spaces.
"""

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
