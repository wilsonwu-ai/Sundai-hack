-- Sundai Bank mock bank. All ids are deterministic strings; all times are ISO 8601 UTC (simulated clock).

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE products (
    product_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('checking','savings','credit_card','loan')),
    monthly_fee  REAL NOT NULL,
    apy          REAL NOT NULL,
    fee_tier     TEXT NOT NULL CHECK (fee_tier IN ('low','high')),
    min_balance  REAL NOT NULL,
    description  TEXT NOT NULL
);

CREATE TABLE customers (
    customer_id       TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    email             TEXT NOT NULL,
    phone             TEXT NOT NULL,
    address           TEXT NOT NULL,
    segment           TEXT NOT NULL CHECK (segment IN ('standard','premium','small_business')),
    kyc_status        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    password_resets   INTEGER NOT NULL DEFAULT 0,
    last_password_reset TEXT,
    annual_income     REAL NOT NULL
);

CREATE TABLE accounts (
    account_id     TEXT PRIMARY KEY,
    customer_id    TEXT NOT NULL REFERENCES customers(customer_id),
    kind           TEXT NOT NULL CHECK (kind IN ('checking','savings','credit_card','loan')),
    product_id     TEXT REFERENCES products(product_id),
    balance        REAL NOT NULL,          -- positive = money held; for credit_card/loan, amount owed
    status         TEXT NOT NULL,
    opened_at      TEXT NOT NULL,
    days_past_due  INTEGER NOT NULL DEFAULT 0,
    late_fee       REAL NOT NULL DEFAULT 0,
    plan_status    TEXT
);

CREATE TABLE transactions (
    txn_id       TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL REFERENCES accounts(account_id),
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    kind         TEXT NOT NULL,            -- purchase, ach_out, wire_out, credit, deposit, fee
    amount       REAL NOT NULL,            -- positive = money out of the account (a debit); negative = money in
    merchant     TEXT NOT NULL,
    category     TEXT NOT NULL,
    posted_at    TEXT NOT NULL,
    anomaly      TEXT,                     -- duplicate_charge | unrecognized_merchant | wrong_amount | NULL
    status       TEXT NOT NULL DEFAULT 'posted'
);

CREATE TABLE beneficiaries (
    beneficiary_id  TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    name            TEXT NOT NULL,
    routing_number  TEXT NOT NULL,
    account_last4   TEXT NOT NULL,
    added_at        TEXT NOT NULL,
    payments_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE cards (
    card_id      TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    last4        TEXT NOT NULL,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL             -- active | frozen
);

CREATE TABLE disputes (
    dispute_id       TEXT PRIMARY KEY,
    customer_id      TEXT NOT NULL REFERENCES customers(customer_id),
    txn_id           TEXT NOT NULL REFERENCES transactions(txn_id),
    reason           TEXT NOT NULL,
    status           TEXT NOT NULL,        -- open | credited | denied | closed
    credited_amount  REAL NOT NULL DEFAULT 0,
    opened_at        TEXT NOT NULL,
    closed_at        TEXT
);

CREATE TABLE aml_alerts (
    alert_id     TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    txn_id       TEXT,
    kind         TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('low','medium','high')),
    status       TEXT NOT NULL,            -- open | dismissed | escalated | sar_filed
    source       TEXT NOT NULL,            -- monitoring | payments
    created_at   TEXT NOT NULL,
    resolved_at  TEXT,
    resolved_by  TEXT
);

CREATE TABLE loan_applications (
    application_id  TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    amount          REAL NOT NULL,
    purpose         TEXT NOT NULL,
    stated_income   REAL NOT NULL,
    status          TEXT NOT NULL,         -- pending | approved | denied
    credit_score    INTEGER,
    verified_income REAL,
    score           INTEGER,
    submitted_at    TEXT NOT NULL,
    decided_at      TEXT,
    source          TEXT NOT NULL          -- web | sales
);

CREATE TABLE ledger_entries (
    entry_id        TEXT PRIMARY KEY,
    ref             TEXT NOT NULL,
    booked_amount   REAL NOT NULL,
    expected_amount REAL NOT NULL,
    status          TEXT NOT NULL,         -- clean | discrepancy | adjusted
    memo            TEXT NOT NULL,
    booked_at       TEXT NOT NULL
);

CREATE TABLE access_grants (
    grant_id    TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    resource    TEXT NOT NULL,
    level       TEXT NOT NULL,
    granted_at  TEXT NOT NULL
);

CREATE TABLE messages (
    message_id   TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    channel      TEXT NOT NULL,            -- inbox | sms | email | phone
    direction    TEXT NOT NULL,            -- outbound | inbound
    sender       TEXT NOT NULL,            -- agent id or 'system'
    subject      TEXT NOT NULL,
    body         TEXT NOT NULL,
    tone         TEXT,
    sent_at      TEXT NOT NULL
);

CREATE INDEX idx_txn_customer ON transactions(customer_id, posted_at);
CREATE INDEX idx_acct_customer ON accounts(customer_id);
CREATE INDEX idx_ben_customer ON beneficiaries(customer_id);
CREATE INDEX idx_msg_customer ON messages(customer_id, sent_at);
