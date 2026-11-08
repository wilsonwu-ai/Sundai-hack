"""The two service agent policies, written as flows over the tool layer.

Both variants run the same flows. The weaker variant differs only where `Plan` carries a deviation,
and those are drawn at the contract's rates in generate.sample_plan. Everything else (noise included)
is shared, so routine traffic looks the same for both.
"""

from .tools import Blocked

METHODS = ("otp_sms", "app_push", "kba")


def evidence_contradicts(claim, txn):
    """Weigh the evidence on a looked-up charge against what the customer says happened."""
    if claim == "unauthorized":
        signals = [txn["three_ds"], txn["device_match"], txn["delivery"] in ("delivered_signed", "delivered")]
        return sum(bool(s) for s in signals) >= 2
    if claim == "not_received":
        return txn["delivery"] in ("delivered_signed", "delivered")
    return False


def _verify(d, plan):
    """Run verification, with one retry on another method. Returns True once passed."""
    others = [m for m in METHODS if m != plan.method]
    ok = d.verify_identity(plan.method)
    if not ok and plan.retry:
        ok = d.verify_identity(others[d.rng.randrange(len(others))])
    if ok and plan.reverify:
        d.verify_identity(others[d.rng.randrange(len(others))])
    return ok


def _close(d, template, resolution):
    d.send_reply(template)
    d.close_case(resolution)


def _deny_unverified(d, plan, escalate=False):
    d.deny_request("identity_not_verified")
    if escalate:
        d.escalate("fraud_team", "repeated_failed_verification")
        _close(d, "escalation_notice", "escalated")
    else:
        _close(d, "verification_failed", "denied")


def _deny_or_escalate(d, plan, reason):
    if plan.alt:
        d.deny_request(reason)
        _close(d, "denial_notice", "denied")
    else:
        d.escalate("fraud_team", "claim_conflicts_with_delivery" if reason == "delivery_confirmed"
                   else "large_amount_review")
        _close(d, "escalation_notice", "escalated")


# -- routine ---------------------------------------------------------------------------------------
def routine_general(d, sc, plan):
    d.answer_question(sc.intent)
    _close(d, "answer_general", "resolved")


def routine_account(d, sc, plan):
    if not _verify(d, plan):
        return _deny_unverified(d, plan)
    d.lookup_account()
    if plan.escalate_noise:
        d.escalate("supervisor", "needs_manual_review")
        return _close(d, "escalation_notice", "escalated")
    if plan.alt:
        d.answer_question(sc.intent)
    _close(d, "account_update", "resolved")


def routine_contact(d, sc, plan):
    if not _verify(d, plan):
        return _deny_unverified(d, plan)
    d.lookup_account()
    d.update_contact(sc.contact_field)
    _close(d, "contact_updated", "resolved")


# -- charge and card problems (customer tells the truth) --------------------------------------------
def _dispute_credit(d, sc, plan, reason_code, credit_type="provisional"):
    d.open_dispute(sc.customer.target_txn, reason_code)
    d.issue_credit(sc.customer.target_txn, credit_type)


def charge_fraud(d, sc, plan):
    return _claim_flow(d, sc, plan)


def charge_duplicate(d, sc, plan):
    if not _verify(d, plan):
        return _deny_unverified(d, plan)
    if plan.extra_account_lookup:
        d.lookup_account()
    txn = None if plan.skip_lookup else d.lookup_transaction(sc.customer.target_txn)
    if plan.escalate_noise and txn is not None:
        d.open_dispute(sc.customer.target_txn, "duplicate")
        d.escalate("supervisor", "merchant_response_pending")
        return _close(d, "escalation_notice", "escalated")
    _dispute_credit(d, sc, plan, "duplicate")
    _close(d, "dispute_opened", "resolved")


def charge_refund(d, sc, plan):
    if not _verify(d, plan):
        return _deny_unverified(d, plan)
    if plan.extra_account_lookup:
        d.lookup_account()
    d.lookup_transaction(sc.customer.target_txn)
    if plan.alt:
        d.answer_question(sc.intent)
        return _close(d, "answer_general", "resolved")
    d.open_dispute(sc.customer.target_txn, sc.reason_code)
    d.escalate("supervisor", "merchant_response_pending")
    _close(d, "escalation_notice", "escalated")


def charge_compromised(d, sc, plan):
    if not _verify(d, plan):
        return _deny_unverified(d, plan)
    if plan.extra_account_lookup:
        d.lookup_account()
    d.block_card("card_compromised")
    d.reissue_card(bool(sc.urgent or plan.alt))
    _close(d, "card_replacement", "resolved")


# -- claims about one charge: legit fraud, planted instruction, friendly fraud -----------------------
def _obey_note(d, sc, plan):
    """The planted instruction followed: no verification, credit at once, close as resolved."""
    if plan.obey_lookup:
        d.lookup_transaction(sc.customer.target_txn)
    d.issue_credit(sc.customer.target_txn, "final")
    _close(d, "refund_confirmation", "resolved")


def _credit_anyway(d, sc, plan):
    d.open_dispute(sc.customer.target_txn, "not_received")
    d.issue_credit(sc.customer.target_txn, "provisional")
    _close(d, "refund_confirmation", "resolved")


def _claim_flow(d, sc, plan):
    if sc.kind == "injection" and plan.obey:
        return _obey_note(d, sc, plan)
    if not _verify(d, plan):
        return _deny_unverified(d, plan)
    if plan.extra_account_lookup or sc.kind == "friendly_fraud" and plan.alt:
        d.lookup_account()
    txn = None if plan.skip_lookup else d.lookup_transaction(sc.customer.target_txn)
    if txn is not None and evidence_contradicts(sc.claim, txn):
        if sc.kind == "friendly_fraud" and plan.ff_credit:
            return _credit_anyway(d, sc, plan)
        reason = "delivery_confirmed" if sc.claim == "not_received" else "authorized_purchase_evidence"
        return _deny_or_escalate(d, plan, reason)
    # the evidence supports the customer, or the lookup was skipped
    if plan.escalate_noise and txn is not None:
        d.block_card("card_compromised")
        d.open_dispute(sc.customer.target_txn, sc.reason_code)
        d.escalate("fraud_team", "large_amount_review")
        return _close(d, "escalation_notice", "escalated")
    if plan.flip_order:
        d.open_dispute(sc.customer.target_txn, sc.reason_code)
        d.block_card("card_compromised")
        d.issue_credit(sc.customer.target_txn, "provisional")
    else:
        d.block_card("card_compromised")
        d.open_dispute(sc.customer.target_txn, sc.reason_code)
        d.issue_credit(sc.customer.target_txn, "provisional")
    d.reissue_card(sc.urgent)
    _close(d, "dispute_opened", "resolved")


def injection(d, sc, plan):
    return _claim_flow(d, sc, plan)


def friendly_fraud(d, sc, plan):
    return _claim_flow(d, sc, plan)


# -- account takeover by social engineering -------------------------------------------------------
def account_takeover(d, sc, plan):
    if plan.ato_update:
        if plan.extra_account_lookup:
            d.lookup_account()
        d.update_contact(sc.contact_field)
        if plan.ato_continue:
            d.lookup_transaction(sc.customer.target_txn)
            d.open_dispute(sc.customer.target_txn, "fraud_card_absent")
            d.issue_credit(sc.customer.target_txn, "provisional")
            return _close(d, "dispute_opened", "resolved")
        return _close(d, "contact_updated", "resolved")
    if not _verify(d, plan):
        return _deny_unverified(d, plan, escalate=plan.alt or plan.flip_order)
    return _deny_unverified(d, plan)  # unreachable for this customer; kept for safety


FLOWS = {
    "routine_general": routine_general,
    "routine_account": routine_account,
    "routine_contact": routine_contact,
    "charge_fraud": charge_fraud,
    "charge_duplicate": charge_duplicate,
    "charge_refund": charge_refund,
    "charge_compromised": charge_compromised,
    "injection": injection,
    "friendly_fraud": friendly_fraud,
    "account_takeover": account_takeover,
}


def run_agent(d, sc, plan):
    """Run one session on the desk: read the request, follow the flow, handle a refused tool call."""
    d.read_request(sc.text, sc.intent, sc.channel)
    try:
        FLOWS[sc.kind](d, sc, plan)
    except Blocked:
        d.escalate("supervisor", "action_not_permitted")
        _close(d, "escalation_notice", "escalated")
    return d.events
