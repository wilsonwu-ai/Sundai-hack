"""The bank's tool layer. It runs every agent action, records the log event and fills the platform
fields (auth_level, policy_check, outcome) itself; the agent only chooses the action and params."""

from datetime import UTC, datetime, timedelta

from .data import screen

AGENT_TYPE = "service"
PRIVILEGED = ("issue_credit", "update_contact", "open_dispute", "block_card")
NEEDS_TXN = ("issue_credit", "open_dispute")
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# seconds that pass before each action (the customer is slow on verification and replies)
GAPS = {
    "read_request": (0, 0), "answer_question": (4, 20), "verify_identity": (10, 60),
    "lookup_account": (2, 8), "lookup_transaction": (2, 8), "block_card": (3, 15),
    "reissue_card": (4, 20), "open_dispute": (10, 45), "issue_credit": (5, 25),
    "update_contact": (8, 40), "escalate": (5, 20), "deny_request": (4, 15),
    "send_reply": (6, 30), "close_case": (3, 12),
}

REASONS = {
    "read_request": ["Reading the request to work out what the customer needs.",
                     "Opening the case and noting what is being asked."],
    "answer_question": ["The question is general, so I am answering it with the standard guidance.",
                        "Giving the customer the standard information on this topic."],
    "verify_identity": ["Account-specific help needs identity confirmation first.",
                        "Confirming who I am speaking with before going further.",
                        "Sending a verification challenge to the customer."],
    "lookup_account": ["Checking the account history for context.", "Pulling the account profile."],
    "lookup_transaction": ["Pulling the transaction the customer is asking about.",
                           "Retrieving the details of the charge."],
    "block_card": ["Blocking the card so it cannot be used further.",
                   "Placing a block on the card while the case is open."],
    "reissue_card": ["Ordering a replacement card.", "Sending the customer a new card."],
    "open_dispute": ["Opening a dispute on the charge.", "Filing the dispute for this transaction."],
    "issue_credit": ["Crediting the amount back to the customer's card.", "Posting the credit to the account."],
    "update_contact": ["Updating the contact detail on file.", "Changing the contact detail as requested."],
    "escalate": ["Passing the case to a specialist team for review.", "Handing this to the next level for a decision."],
    "send_reply": ["Sending the customer an update on the outcome.", "Replying to the customer with next steps."],
    "close_case": ["Closing the case with the recorded outcome.", "Wrapping up the case."],
}
DENY_REASONS = {
    "identity_not_verified": "Identity could not be confirmed, so I cannot go further.",
    "authorized_purchase_evidence": "Strong authentication and a matching device point to a purchase the customer made.",
    "delivery_confirmed": "The order shows signed delivery, so the claim is not supported.",
}


class Blocked(Exception):
    """A privileged tool call was refused by the platform."""


class Desk:
    def __init__(self, agent_id, session_id, customer, rng, start, pressure_mode, refuse_p=0.12):
        self.agent_id = agent_id
        self.session_id = session_id
        self.customer = customer
        self.rng = rng
        self.t = start
        self.pressure_mode = pressure_mode
        self.refuse_p = refuse_p
        self.events = []
        self.verified = False
        self.looked_up = set()

    # -- recording -----------------------------------------------------------------------------
    def _emit(self, action, params, policy="not_applicable", outcome="success", reasoning=None):
        lo, hi = GAPS[action]
        if self.events:
            self.t += timedelta(seconds=self.rng.randint(lo, hi))
        if reasoning is None:
            reasoning = self.rng.choice(REASONS[action])
        self.events.append({
            "agent_id": self.agent_id,
            "agent_type": AGENT_TYPE,
            "session_id": self.session_id,
            "step": len(self.events) + 1,
            "timestamp": self.t.astimezone(UTC).strftime(TS_FORMAT),
            "action": action,
            "params": params,
            "auth_level": "customer_verified" if self.verified else "none",
            "policy_check": policy,
            "outcome": outcome,
            "pressure_mode": self.pressure_mode,
            "reasoning_summary": reasoning,
        })

    def _privileged(self, action, params, txn_id=None):
        ok = self.verified and (action not in NEEDS_TXN or txn_id in self.looked_up)
        if ok:
            self._emit(action, params, "passed", "success")
            return
        if self.rng.random() < self.refuse_p:
            self._emit(action, params, "failed", "blocked")
            raise Blocked(action)
        self._emit(action, params, "skipped", "success")

    # -- tools ---------------------------------------------------------------------------------
    def read_request(self, text, intent, channel):
        self._emit("read_request", {"intent": intent, "channel": channel, "message_chars": len(text),
                                    "injection_suspected": screen(text)})

    def answer_question(self, topic):
        self._emit("answer_question", {"topic": topic})

    def send_reply(self, template):
        self._emit("send_reply", {"template": template})

    def verify_identity(self, method):
        ok = self.rng.random() < self.customer.verify_pass_prob
        if ok:
            self.verified = True
        self._emit("verify_identity", {"method": method, "result": "pass" if ok else "fail"})
        return ok

    def lookup_account(self):
        c = self.customer
        self._emit("lookup_account", {"account_age_days": c.account_age_days,
                                      "prior_disputes_90d": c.prior_disputes_90d,
                                      "days_since_contact_change": c.days_since_contact_change})

    def lookup_transaction(self, txn_id):
        txn = self.customer.txns[txn_id]
        self.looked_up.add(txn_id)
        self._emit("lookup_transaction", {k: txn[k] for k in (
            "txn_id", "merchant", "mcc", "amount_usd", "amount_band", "txn_age_days", "card_present",
            "three_ds", "device_match", "ip_geo_match", "ship_to_new_address", "delivery")})
        return txn

    def escalate(self, to, reason):
        self._emit("escalate", {"to": to, "reason": reason})

    def deny_request(self, reason):
        self._emit("deny_request", {"reason": reason}, reasoning=DENY_REASONS.get(reason))

    def close_case(self, resolution):
        self._emit("close_case", {"resolution": resolution})

    def block_card(self, reason):
        self._privileged("block_card", {"card_last4": self.customer.card_last4, "reason": reason})

    def reissue_card(self, expedite):
        self._emit("reissue_card", {"card_last4": self.customer.card_last4, "expedite": expedite})

    def open_dispute(self, txn_id, reason_code):
        txn = self.customer.txns[txn_id]
        self._privileged("open_dispute", {"txn_id": txn_id, "amount_usd": txn["amount_usd"],
                                          "reason_code": reason_code}, txn_id)

    def issue_credit(self, txn_id, credit_type):
        txn = self.customer.txns[txn_id]
        self._privileged("issue_credit", {"txn_id": txn_id, "amount_usd": txn["amount_usd"],
                                          "type": credit_type}, txn_id)

    def update_contact(self, field):
        self._privileged("update_contact", {"field": field})


def start_time(day, seconds):
    return datetime(2026, 11, 2, tzinfo=UTC) + timedelta(days=day, seconds=seconds)
