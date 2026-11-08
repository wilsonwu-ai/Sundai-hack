"""Violation categories: what kind of problem a step is, for any kind of agent.

A step gets categories from two sources:

- the role controls it breaks (risk/scoring/roles.py): each flag id maps to a
  category in FLAG_CATEGORY. A new kind of agent maps its rules into the same
  list; an unmapped flag lands in "other_controls", so nothing is dropped;
- signals that apply to every agent: its policy check was skipped or failed,
  or BehaviorGPT found a high-severity step unlikely (an unusual sequence).

The dashboard reads the list from GET /categories, so adding a category here
is all it takes to show it.
"""

CATEGORIES = [
    {"id": "payment_controls", "name": "Payment controls",
     "description": "Money sent around the payment controls: a new payee paid without 2FA, one payment split into several."},
    {"id": "account_takeover", "name": "Account takeover",
     "description": "Contact details changed and then the password reset, or account changes with no verified customer."},
    {"id": "data_access", "name": "Customer data access",
     "description": "Acting on a customer other than the one the session is for."},
    {"id": "privilege", "name": "Access and privilege",
     "description": "Raising its own access, then using it."},
    {"id": "due_diligence", "name": "Skipped due diligence",
     "description": "Deciding before the check that should come first: crediting without the transaction, dismissing without the history, lending without income or credit."},
    {"id": "limits", "name": "Authority limits",
     "description": "Moving more than the role may in one action."},
    {"id": "bulk_actions", "name": "Bulk actions",
     "description": "One action applied to many items at once."},
    {"id": "policy_bypass", "name": "Policy check bypassed",
     "description": "The step's own policy check was skipped or failed."},
    {"id": "unusual_sequence", "name": "Unusual sequence",
     "description": "BehaviorGPT found a high-severity step unlikely after the steps before it."},
    {"id": "other_controls", "name": "Other controls",
     "description": "A role control that has no category yet."},
]
CATEGORY_IDS = [c["id"] for c in CATEGORIES]

FLAG_CATEGORY = {
    "new_payee_without_2fa": "payment_controls",
    "split_payment": "payment_controls",
    "reset_after_contact_change": "account_takeover",
    "unverified_change": "account_takeover",
    "other_customer": "data_access",
    "access_then_adjust": "privilege",
    "credit_without_txn_check": "due_diligence",
    "dismiss_without_history": "due_diligence",
    "decision_without_income_check": "due_diligence",
    "decision_without_credit_check": "due_diligence",
    "adjust_without_reconcile": "due_diligence",
    "over_limit": "limits",
    "bulk_dismiss": "bulk_actions",
}

# the event fields each flag is about, for the dashboard to highlight
FLAG_FIELDS = {
    "over_limit": ["amount_usd", "amount", "fee_usd", "loan_amount_usd", "principal_usd", "adjustment_usd"],
    "other_customer": ["customer_id"],
    "unverified_change": ["auth_level"],
    "split_payment": ["beneficiary_id", "amount_usd"],
    "bulk_dismiss": ["alert_ids"],
    "new_payee_without_2fa": ["beneficiary_id"],
    "credit_without_txn_check": ["txn_id"],
    "dismiss_without_history": ["alert_id", "alert_ids"],
}


def flag_info() -> dict:
    """flag id -> its category and the fields to highlight."""
    return {
        flag: {"category": category, "fields": FLAG_FIELDS.get(flag, [])}
        for flag, category in FLAG_CATEGORY.items()
    }


UNUSUAL_SURPRISE = 0.5  # surprise at or above this...
UNUSUAL_SEVERITY = 0.5  # ...on a step at least this severe is an unusual sequence


def categorize(event: dict, step: dict) -> list[str]:
    """The step's categories, in CATEGORIES order."""
    found = {FLAG_CATEGORY.get(flag, "other_controls") for flag in step.get("flags", [])}
    if event["policy_check"] in ("skipped", "failed"):
        found.add("policy_bypass")
    if "expected" in step and step["surprise"] >= UNUSUAL_SURPRISE and step["severity"] >= UNUSUAL_SEVERITY:
        found.add("unusual_sequence")
    return [c for c in CATEGORY_IDS if c in found]
