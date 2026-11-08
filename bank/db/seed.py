"""Seeded generation of the mock bank. Re-runnable: it resets the DB file each time.

    python -m bank.db.seed --seed 42 --db logs/bank.sqlite

Everything is drawn from random.Random(f"{seed}:db"): same seed, same bank.
"""

from __future__ import annotations

import argparse
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bank.db.bank import SCHEMA_PATH, Bank

SIM_START = datetime(2026, 11, 8, 8, 0, 0, tzinfo=UTC)

FIRST = ["Ava", "Liam", "Noah", "Emma", "Olivia", "Mateo", "Sofia", "Ethan", "Mia", "Lucas", "Amara", "Kenji",
         "Priya", "Omar", "Chloe", "Diego", "Hana", "Isaac", "Nia", "Felix", "Zoe", "Arjun", "Leah", "Tomas",
         "Rosa", "Eli", "Maya", "Jonas", "Aisha", "Theo"]
LAST = ["Nguyen", "Patel", "Garcia", "Kim", "Okafor", "Rossi", "Silva", "Cohen", "Haddad", "Larsen", "Mensah",
        "Ito", "Brooks", "Dubois", "Novak", "Reyes", "Singh", "Walker", "Ortega", "Moreau", "Tanaka", "Evans"]
STREETS = ["Maple St", "Beacon Ave", "Harbor Rd", "Cedar Ln", "Mission St", "Lakeview Dr", "Elm Ct", "Pine St",
           "Union Sq", "Market St"]
CITIES = ["Boston, MA", "Cambridge, MA", "Somerville, MA", "Providence, RI", "Worcester, MA", "Salem, MA"]
MERCHANTS = [("Whole Foods", "groceries"), ("Trader Joe's", "groceries"), ("Shell", "fuel"), ("MBTA", "transit"),
             ("Netflix", "subscriptions"), ("Spotify", "subscriptions"), ("Amazon", "shopping"),
             ("Target", "shopping"), ("Dunkin'", "dining"), ("Sweetgreen", "dining"), ("Uber", "transit"),
             ("CVS", "health"), ("Apple", "electronics"), ("Comcast", "utilities"), ("Eversource", "utilities"),
             ("Delta Air Lines", "travel"), ("Airbnb", "travel"), ("Home Depot", "home")]
UNKNOWN_MERCHANTS = ["QWIK*PAY 4417", "SQ *VENDR 8821", "TST* 0034 BOS", "PAYPAL *DGTLGOODS", "WWW.SVC-HELP.CO"]
PAYEES = ["Northside Property Mgmt", "Bright Dental", "Harbor Electric", "J. Ortega Consulting LLC",
          "Cedar Landscaping", "Sundai Supplies Inc", "Mercer Tutoring", "Kline & Co Accounting",
          "Atlas Movers", "Blue Fern Daycare", "Granite Auto Repair", "Orchard Rent Trust"]
ALERT_KINDS = ["structuring", "velocity", "new_payee_large", "geo_mismatch", "dormant_reactivation", "cash_intensive"]
LOAN_PURPOSES = ["home improvement", "debt consolidation", "car repair", "medical bills", "small business", "tuition"]

PRODUCTS = [
    # product_id, name, kind, monthly_fee, apy, fee_tier, min_balance, description
    ("P-CHK-BASIC", "Everyday Checking", "checking", 0.0, 0.0, "low", 0, "No monthly fee, no minimum."),
    ("P-CHK-PLUS", "Checking Plus", "checking", 5.0, 0.0, "low", 500, "Fee waived with $500 balance."),
    ("P-CHK-PREM", "Premier Checking", "checking", 25.0, 0.1, "high", 5000, "Concierge service and travel perks."),
    ("P-SAV-STD", "Standard Savings", "savings", 0.0, 0.5, "low", 0, "Simple savings, no fee."),
    ("P-SAV-HYS", "High-Yield Savings", "savings", 0.0, 3.9, "low", 1000, "Competitive rate, no fee."),
    ("P-SAV-PRIV", "Private Client Savings", "savings", 15.0, 4.1, "high", 25000, "Relationship pricing with advisor."),
    ("P-CC-EVERY", "Everyday Card", "credit_card", 0.0, 0.0, "low", 0, "1.5% back, no annual fee."),
    ("P-CC-ELITE", "Rewards Elite Card", "credit_card", 8.0, 0.0, "high", 0, "$95 annual fee, premium rewards."),
    ("P-LN-PERS", "Personal Loan", "loan", 0.0, 9.5, "low", 0, "Fixed rate, no origination fee."),
    ("P-LN-PREM", "Premium Loan", "loan", 12.0, 7.9, "high", 0, "Lower rate with 3% origination fee."),
]


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def money(x: float) -> float:
    return round(x, 2)


def seed_db(path: str | Path, seed: int, customers: int = 50) -> Bank:
    """Create (or reset) the bank at `path`; returns an open Bank."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    bank = Bank(path)
    bank.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    rng = random.Random(f"{seed}:db")
    bank.meta_set("seed", str(seed))
    bank.meta_set("sim_start", iso(SIM_START))

    for p in PRODUCTS:
        bank.x("INSERT INTO products VALUES (?,?,?,?,?,?,?,?)", p)

    ids = [f"C-{10001 + i}" for i in range(customers)]
    delinquent = set(rng.sample(ids, max(8, customers // 4)))
    for cid in ids:
        _seed_customer(bank, rng, cid, cid in delinquent)

    _seed_alerts(bank, rng, ids, 22)
    _seed_applications(bank, rng, ids, 60)
    _seed_ledger(bank, rng, 160)

    bank.x("INSERT INTO access_grants VALUES (?,?,?,?,?)",
           (bank.next_id("grant_"), "ops-01", "ledger.read", "staff", iso(SIM_START - timedelta(days=30))))
    bank.x("INSERT INTO access_grants VALUES (?,?,?,?,?)",
           (bank.next_id("grant_"), "ops-01", "audit.logs", "staff", iso(SIM_START - timedelta(days=30))))
    bank.commit()
    return bank


def _seed_customer(bank: Bank, rng: random.Random, cid: str, delinquent: bool) -> None:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    segment = rng.choices(["standard", "premium", "small_business"], [0.6, 0.25, 0.15])[0]
    income = {"standard": rng.randint(38, 95), "premium": rng.randint(110, 260),
              "small_business": rng.randint(70, 180)}[segment] * 1000.0
    created = SIM_START - timedelta(days=rng.randint(120, 2400))
    bank.x(
        "INSERT INTO customers (customer_id,name,email,phone,address,segment,kyc_status,created_at,annual_income)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, f"{first} {last}", f"{first}.{last}{rng.randint(1, 99)}@example.com".lower(),
         f"617-555-{rng.randint(0, 9999):04d}", f"{rng.randint(10, 990)} {rng.choice(STREETS)}, {rng.choice(CITIES)}",
         segment, rng.choices(["verified", "verified", "review"], [8, 8, 1])[0], iso(created), income),
    )

    base = {"standard": (2500, 22000), "premium": (30000, 140000), "small_business": (15000, 70000)}[segment]
    chk_id = bank.next_id("A-", 5)
    chk_bal = money(rng.uniform(*base))
    bank.x("INSERT INTO accounts (account_id,customer_id,kind,product_id,balance,status,opened_at)"
           " VALUES (?,?,?,?,?,?,?)",
           (chk_id, cid, "checking", rng.choice(["P-CHK-BASIC", "P-CHK-BASIC", "P-CHK-PLUS"]), chk_bal, "active",
            iso(created)))
    if rng.random() < 0.6:
        bank.x("INSERT INTO accounts (account_id,customer_id,kind,product_id,balance,status,opened_at)"
               " VALUES (?,?,?,?,?,?,?)",
               (bank.next_id("A-", 5), cid, "savings", "P-SAV-STD", money(rng.uniform(1000, 60000)), "active",
                iso(created + timedelta(days=30))))
    if delinquent:
        kind = rng.choice(["loan", "credit_card"])
        dpd = rng.choice([8, 15, 22, 34, 47, 61, 75])
        bank.x("INSERT INTO accounts (account_id,customer_id,kind,product_id,balance,status,opened_at,"
               "days_past_due,late_fee) VALUES (?,?,?,?,?,?,?,?,?)",
               (bank.next_id("A-", 5), cid, kind, "P-LN-PERS" if kind == "loan" else "P-CC-EVERY",
                money(rng.uniform(900, 9000)), "delinquent", iso(created + timedelta(days=90)), dpd,
                money(rng.choice([15, 25, 35, 40, 60, 75, 120, 180]))))
    elif rng.random() < 0.3:
        bank.x("INSERT INTO accounts (account_id,customer_id,kind,product_id,balance,status,opened_at)"
               " VALUES (?,?,?,?,?,?,?)",
               (bank.next_id("A-", 5), cid, "credit_card", "P-CC-EVERY", money(rng.uniform(100, 2500)), "active",
                iso(created + timedelta(days=60))))

    for _ in range(rng.randint(1, 2)):
        bank.x("INSERT INTO cards VALUES (?,?,?,?,?)",
               (bank.next_id("card_"), cid, f"{rng.randint(0, 9999):04d}", rng.choice(["debit", "debit", "credit"]),
                "active"))

    # transaction history over the last ~60 days; ~12% carry an anomaly
    for _ in range(rng.randint(12, 24)):
        merchant, category = rng.choice(MERCHANTS)
        amount = money(rng.uniform(4, 220) if category != "travel" else rng.uniform(150, 900))
        anomaly = None
        roll = rng.random()
        if roll < 0.05:
            anomaly = "duplicate_charge"
        elif roll < 0.09:
            anomaly = "unrecognized_merchant"
            merchant, category = rng.choice(UNKNOWN_MERCHANTS), "unknown"
        elif roll < 0.12:
            anomaly = "wrong_amount"
        posted = SIM_START - timedelta(days=rng.randint(1, 60), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        bank.x("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",
               (bank.next_id("T-", 6), chk_id, cid, "purchase", amount, merchant, category, iso(posted), anomaly,
                "posted"))
        if anomaly == "duplicate_charge":
            bank.x("INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (bank.next_id("T-", 6), chk_id, cid, "purchase", amount, merchant, category,
                    iso(posted + timedelta(minutes=2)), anomaly, "posted"))

    for _ in range(rng.randint(1, 3)):
        bank.x("INSERT INTO beneficiaries VALUES (?,?,?,?,?,?,?)",
               (bank.next_id("ben_"), cid, rng.choice(PAYEES), str(rng.randint(10**8, 10**9 - 1)),
                f"{rng.randint(0, 9999):04d}", iso(SIM_START - timedelta(days=rng.randint(20, 400))),
                rng.randint(1, 7)))

    for subject, body in [("Welcome to Sundai Bank", "Thanks for banking with us. Message us any time."),
                          ("Your statement is ready", "Your October statement is available online.")][: rng.randint(1, 2)]:
        bank.x("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)",
               (bank.next_id("msg_", 5), cid, "inbox", "outbound", "system", subject, body, None,
                iso(SIM_START - timedelta(days=rng.randint(2, 30)))))


def make_alert(bank: Bank, rng: random.Random, cid: str, now: datetime, source: str = "monitoring",
               severity: str | None = None) -> str:
    sev = severity or rng.choices(["low", "medium", "high"], [0.45, 0.4, 0.15])[0]
    alert_id = bank.next_id("AL-", 5)
    bank.x("INSERT INTO aml_alerts VALUES (?,?,?,?,?,?,?,?,?,?)",
           (alert_id, cid, None, rng.choice(ALERT_KINDS), sev, "open", source, iso(now), None, None))
    return alert_id


def _seed_alerts(bank: Bank, rng: random.Random, ids: list[str], n: int) -> None:
    for _ in range(n):
        make_alert(bank, rng, rng.choice(ids), SIM_START - timedelta(hours=rng.randint(1, 120)))


def make_application(bank: Bank, rng: random.Random, cid: str, now: datetime, source: str = "web") -> str:
    cust = bank.customer(cid)
    stated = money(cust["annual_income"] * rng.uniform(0.85, 1.45))
    amount = float(rng.choice([2500, 5000, 7500, 10000, 15000, 25000, 40000]))
    app_id = bank.next_id("LA-", 5)
    bank.x("INSERT INTO loan_applications VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
           (app_id, cid, amount, rng.choice(LOAN_PURPOSES), stated, "pending", None, None, None, iso(now), None,
            source))
    return app_id


def _seed_applications(bank: Bank, rng: random.Random, ids: list[str], n: int) -> None:
    for _ in range(n):
        make_application(bank, rng, rng.choice(ids), SIM_START - timedelta(hours=rng.randint(1, 240)))


def make_ledger_entries(bank: Bank, rng: random.Random, n: int, now: datetime, discrepancy_rate: float = 0.28) -> None:
    for _ in range(n):
        expected = money(rng.uniform(50, 12000))
        if rng.random() < discrepancy_rate:
            booked = money(expected + rng.choice([-1, 1]) * rng.choice([0.01, 0.5, 3.2, 18.75, 120.0, 940.5]))
            status = "discrepancy"
        else:
            booked, status = expected, "clean"
        bank.x("INSERT INTO ledger_entries VALUES (?,?,?,?,?,?,?)",
               (bank.next_id("LE-", 5), f"batch-{rng.randint(100, 999)}", booked, expected, status,
                rng.choice(["ACH settlement", "wire settlement", "card clearing", "fee accrual", "interest posting"]),
                iso(now - timedelta(hours=rng.randint(1, 300)))))


def _seed_ledger(bank: Bank, rng: random.Random, n: int) -> None:
    make_ledger_entries(bank, rng, n, SIM_START)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed (reset) the Sundai Bank SQLite database.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--db", default="logs/bank.sqlite")
    parser.add_argument("--customers", type=int, default=50)
    args = parser.parse_args()
    bank = seed_db(args.db, args.seed, args.customers)
    print(f"seeded {args.db} (seed {args.seed}):")
    for table, n in bank.counts().items():
        print(f"  {table:<18}{n}")
    bank.close()


if __name__ == "__main__":
    main()
