"""Severity: how much harm an action can do if it shouldn't have happened, 0 to 1.

Team 2's table (PRD section 7), re-keyed from the PRD appendix to the names in
shared/actions.md. A skipped or failed policy check is a strong signal on its
own, so it raises the weight; so does elevated access. Capped at 1.
"""

WEIGHTS = {
    # support
    "get_balance": 0.1, "freeze_card": 0.3, "update_contact": 0.4, "reset_password": 0.6,
    # payments
    "confirm_2fa": 0.0, "add_beneficiary": 0.7, "send_ach": 0.8, "send_wire": 0.9,
    # disputes
    "open_dispute": 0.1, "fetch_txn": 0.1, "close_case": 0.2, "issue_credit": 0.6,
    # sales
    "get_profile": 0.1, "recommend_product": 0.2, "open_account": 0.4,
    # collections
    "get_delinquency": 0.1, "send_message": 0.3, "offer_plan": 0.3, "waive_fee": 0.5,
    # kyc
    "escalate": 0.0, "review_alert": 0.1, "fetch_history": 0.2, "file_sar": 0.2, "dismiss_alert": 0.6,
    # underwriting
    "verify_income": 0.1, "pull_credit": 0.2, "score_applicant": 0.2, "decide_loan": 0.7,
    # ops
    "match_ledger": 0.1, "read_logs": 0.3, "post_adjustment": 0.7, "request_access": 0.8,
    # service (shares open_dispute, issue_credit, update_contact, escalate, close_case with the rows above)
    "read_request": 0.0, "verify_identity": 0.0, "answer_question": 0.1, "lookup_transaction": 0.1,
    "lookup_account": 0.2, "deny_request": 0.2, "send_reply": 0.2, "block_card": 0.3, "reissue_card": 0.4,
    # netsec
    "read_traffic": 0.0, "check_reputation": 0.0, "check_rate": 0.0, "open_incident": 0.0, "lookup_client": 0.1,
    "flag_request": 0.1, "step_up_auth": 0.1, "throttle_client": 0.3, "block_ip": 0.4, "close_alert": 0.5,
    "revoke_token": 0.5, "raise_rate_limit": 0.7, "allowlist_client": 0.8, "disable_rule": 0.8,
}
DEFAULT_WEIGHT = 0.3  # an action added to shared/actions.md before it gets a weight here

POLICY_BUMP = {"skipped": 0.3, "failed": 0.4}
ELEVATED_BUMP = 0.1


def severity(event: dict) -> tuple[float, list[str]]:
    """The event's severity and the reasons that raised it above the action's weight."""
    weight = WEIGHTS.get(event["action"], DEFAULT_WEIGHT)
    reasons = []
    if bump := POLICY_BUMP.get(event["policy_check"]):
        weight += bump
        reasons.append(f"policy check {event['policy_check']}")
    if event["auth_level"] == "elevated":
        weight += ELEVATED_BUMP
        reasons.append("elevated access")
    return min(weight, 1.0), reasons
