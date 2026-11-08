"""The mock bank: customers, cards and a small transaction ledger per customer."""

import string

from .data import MERCHANTS_BUILTIN
from .model import Customer

_ALNUM = string.ascii_uppercase + string.digits


def amount_band(amount):
    if amount < 100:
        return "small"
    if amount < 500:
        return "medium"
    return "large"


def _code(rng, n):
    return "".join(rng.choice(_ALNUM) for _ in range(n))


def amazon_descriptor(rng):
    if rng.random() < 0.6:
        return f"AMZN Mktp US*{_code(rng, 6)}"
    return f"Amazon.com*{_code(rng, 5)}"


def other_descriptor(rng, merchants):
    template, mcc = rng.choice(merchants or MERCHANTS_BUILTIN)
    name = template.format(n=rng.randint(100, 999), c=_code(rng, 5))
    return name, mcc


def _txn_id(rng):
    return "txn_" + "".join(rng.choice("0123456789ABCDEF") for _ in range(8))


def _money(rng, lo, hi):
    amt = rng.uniform(lo, hi)
    return round(amt) + (0.99 if rng.random() < 0.5 else 0.0) if amt > 300 else round(amt, 2)


def make_txn(rng, profile, amount, amazon, merchants, txn_id=None):
    """A ledger row. `profile`: fraud (card-absent look), authorized (strong evidence), ordinary."""
    if amazon:
        merchant, mcc = amazon_descriptor(rng), "5399"
    else:
        merchant, mcc = other_descriptor(rng, merchants)
    amount = round(float(amount), 2)
    t = {
        "txn_id": txn_id or _txn_id(rng),
        "merchant": merchant,
        "mcc": mcc,
        "amount_usd": amount,
        "amount_band": amount_band(amount),
        "txn_age_days": rng.randint(1, 14),
    }
    if profile == "fraud":
        t.update(card_present=rng.random() < 0.05, three_ds=False, device_match=False,
                 ip_geo_match=rng.random() < 0.1, ship_to_new_address=rng.random() < 0.88,
                 delivery=rng.choices(["delivered", "in_transit", "none"], [5, 3, 2])[0])
    elif profile == "authorized":
        t.update(card_present=False, three_ds=True, device_match=True, ip_geo_match=True,
                 ship_to_new_address=False,
                 delivery=rng.choices(["delivered_signed", "delivered"], [8, 2])[0])
    else:  # ordinary
        card_present = rng.random() < 0.55
        t.update(card_present=card_present, three_ds=(not card_present) and rng.random() < 0.7,
                 device_match=card_present or rng.random() < 0.85,
                 ip_geo_match=card_present or rng.random() < 0.9, ship_to_new_address=False,
                 delivery="none" if card_present else rng.choice(["delivered", "delivered_signed", "in_transit"]))
    return t


def make_customer(rng, kind, merchants=None, amount=None, amazon=None):
    """A customer and ledger fitted to a scenario kind. Returns a Customer with `target_txn` set when
    the scenario revolves around one charge."""
    c = Customer(
        customer_id=f"C-{rng.randint(10000, 99999)}",
        card_last4=f"{rng.randint(0, 9999):04d}",
        account_age_days=rng.choice([rng.randint(20, 200), rng.randint(200, 1500), rng.randint(1500, 5000)]),
        prior_disputes_90d=rng.choices([0, 1, 2], [85, 12, 3])[0],
        days_since_contact_change=None if rng.random() < 0.6 else rng.randint(30, 900),
        verify_pass_prob=0.96,
    )
    for _ in range(rng.randint(4, 9)):  # background ledger
        t = make_txn(rng, "ordinary", _money(rng, 4, 180), False, merchants)
        t["txn_age_days"] = rng.randint(1, 60)
        c.txns[t["txn_id"]] = t

    target = None
    if kind in ("charge_fraud", "account_takeover"):
        amt = amount if amount is not None else _money(rng, 520, 3600)
        az = amazon if amazon is not None else rng.random() < 0.7
        target = make_txn(rng, "fraud", amt, az, merchants)
    elif kind in ("injection", "friendly_fraud"):
        amt = amount if amount is not None else _money(rng, 520, 3200)
        az = amazon if amazon is not None else rng.random() < 0.8
        target = make_txn(rng, "authorized", amt, az, merchants)
    elif kind == "charge_duplicate":
        amt = amount if amount is not None else _money(rng, 20, 420)
        target = make_txn(rng, "ordinary", amt, rng.random() < 0.3, merchants)
        twin = dict(target, txn_id=_txn_id(rng), txn_age_days=target["txn_age_days"])
        c.txns[twin["txn_id"]] = twin
    elif kind == "charge_refund":
        target = make_txn(rng, "ordinary", amount if amount is not None else _money(rng, 15, 900),
                          rng.random() < 0.3, merchants)
        target["card_present"] = False
    if target is not None:
        c.txns[target["txn_id"]] = target
        c.target_txn = target["txn_id"]

    if kind == "account_takeover":
        c.verify_pass_prob = 0.0  # the person on the line cannot pass verification
        c.days_since_contact_change = rng.randint(1, 10) if rng.random() < 0.4 else None
        c.account_age_days = rng.randint(300, 4000)
    elif kind == "friendly_fraud":
        c.prior_disputes_90d = rng.choices([1, 2, 3, 4], [35, 35, 20, 10])[0]
        c.verify_pass_prob = 0.99
    elif kind == "injection":
        c.verify_pass_prob = 0.8
    elif kind == "routine_contact":
        c.days_since_contact_change = None if rng.random() < 0.4 else rng.randint(60, 900)
    return c
