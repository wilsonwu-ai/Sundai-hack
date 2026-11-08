"""Plain data classes shared by the mock bank, the tool layer and the agent policies."""

from dataclasses import dataclass, field


@dataclass
class Customer:
    customer_id: str
    card_last4: str
    account_age_days: int
    prior_disputes_90d: int
    days_since_contact_change: int | None
    verify_pass_prob: float
    txns: dict = field(default_factory=dict)
    target_txn: str | None = None


@dataclass
class Scenario:
    """One customer request and the facts behind it (never logged as such)."""

    kind: str  # routine_general, routine_account, routine_contact, charge_fraud, charge_duplicate,
    #            charge_refund, charge_compromised, injection, friendly_fraud, account_takeover
    customer_kind: str  # legit | fake
    attack: str  # none | injection | friendly_fraud | account_takeover
    intent: str
    channel: str
    text: str
    urgent: bool
    claim: str  # unauthorized | not_received | duplicate | refund | compromised | contact_change | none
    customer: Customer
    reason_code: str = "fraud_card_absent"
    contact_field: str = "email"


@dataclass
class Plan:
    """The agent's decisions for one session, drawn up front so flows stay readable.

    Deviations (obey .. ff_credit) are only ever True for the weaker variant; the noise fields
    are drawn for both variants.
    """

    obey: bool = False  # act on the instruction planted in the message
    skip_lookup: bool = False  # skip the transaction lookup under urgency
    ato_update: bool = False  # change contact details without verification
    ato_continue: bool = False  # after the contact change, go on to dispute and credit
    ff_credit: bool = False  # credit although the evidence contradicts the claim
    obey_lookup: bool = False
    extra_account_lookup: bool = False
    reverify: bool = False
    escalate_noise: bool = False
    flip_order: bool = False
    alt: bool = False
    retry: bool = True
    method: str = "otp_sms"
