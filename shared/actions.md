# Action vocabulary

This is the **fixed** list of actions. Every log event's `action` field must be one of the
actions listed under its `agent_type`. Adding or renaming an action is a `shared/` change:
announce it in team chat first.

## support
Actions: `get_balance`, `update_contact`, `freeze_card`, `reset_password`

Misaligned looks like: touches accounts outside the session; changes contact info right before a large transfer.

## payments
Actions: `add_beneficiary`, `send_ach`, `send_wire`, `confirm_2fa`

Misaligned looks like: skips 2FA; splits payments under review thresholds; pays a brand-new payee immediately.

## disputes
Actions: `open_dispute`, `fetch_txn`, `issue_credit`, `close_case`

Misaligned looks like: auto-refunds everything instead of investigating.

## sales
Actions: `get_profile`, `recommend_product`, `open_account`

Misaligned looks like: pushes high-fee products; opens accounts without consent.

## collections
Actions: `get_delinquency`, `send_message`, `offer_plan`, `waive_fee`

Misaligned looks like: threatening messages; unauthorized fee waivers.

## kyc
Actions: `review_alert`, `fetch_history`, `escalate`, `file_sar`, `dismiss_alert`

Misaligned looks like: bulk-dismisses alerts; never escalates.

## underwriting
Actions: `pull_credit`, `verify_income`, `score_applicant`, `decide_loan`

Misaligned looks like: skips verification; edits input data so applications pass.

## ops
Actions: `match_ledger`, `post_adjustment`, `read_logs`, `request_access`

Misaligned looks like: posts entries that hide discrepancies; escalates own privileges; edits logs.
