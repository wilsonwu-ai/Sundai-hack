"""Thin SQLite wrapper for the mock bank: dict rows, deterministic id counters, read helpers.

Not thread-safe on its own: whoever shares a Bank between threads holds a lock around it
(the simulation engine does).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Bank:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()      # the API serves requests while a run is going

    # -- plumbing ---------------------------------------------------------------

    def q(self, sql: str, args: tuple | list = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def q1(self, sql: str, args: tuple | list = ()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def x(self, sql: str, args: tuple | list = ()) -> None:
        with self.lock:
            self.conn.execute(sql, args)

    def commit(self) -> None:
        with self.lock:
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.commit()
            self.conn.close()

    def next_id(self, prefix: str, width: int = 4) -> str:
        """Deterministic counter ids: ben_0007, T-100231, ..."""
        key = f"seq:{prefix}"
        row = self.q1("SELECT value FROM meta WHERE key = ?", (key,))
        n = int(row["value"]) + 1 if row else 1
        self.x("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(n)))
        return f"{prefix}{n:0{width}d}"

    def meta_get(self, key: str, default: str = "") -> str:
        row = self.q1("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        self.x("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    # -- reads used by tools, task generators and the API ----------------------

    def customers(self) -> list[dict]:
        return self.q("SELECT * FROM customers ORDER BY customer_id")

    def customer(self, customer_id: str) -> dict | None:
        return self.q1("SELECT * FROM customers WHERE customer_id = ?", (customer_id,))

    def accounts(self, customer_id: str) -> list[dict]:
        return self.q("SELECT * FROM accounts WHERE customer_id = ? ORDER BY account_id", (customer_id,))

    def checking(self, customer_id: str) -> dict | None:
        return self.q1(
            "SELECT * FROM accounts WHERE customer_id = ? AND kind = 'checking' ORDER BY account_id",
            (customer_id,),
        )

    def transactions(self, customer_id: str, limit: int = 50) -> list[dict]:
        return self.q(
            "SELECT * FROM transactions WHERE customer_id = ? ORDER BY posted_at DESC, txn_id DESC LIMIT ?",
            (customer_id, limit),
        )

    def beneficiaries(self, customer_id: str) -> list[dict]:
        return self.q("SELECT * FROM beneficiaries WHERE customer_id = ? ORDER BY beneficiary_id", (customer_id,))

    def cards(self, customer_id: str) -> list[dict]:
        return self.q("SELECT * FROM cards WHERE customer_id = ? ORDER BY card_id", (customer_id,))

    def messages(self, customer_id: str, limit: int = 50) -> list[dict]:
        return self.q(
            "SELECT * FROM messages WHERE customer_id = ? ORDER BY sent_at DESC, message_id DESC LIMIT ?",
            (customer_id, limit),
        )

    def disputes(self, customer_id: str) -> list[dict]:
        return self.q("SELECT * FROM disputes WHERE customer_id = ? ORDER BY dispute_id DESC", (customer_id,))

    def products(self, kind: str | None = None) -> list[dict]:
        if kind:
            return self.q("SELECT * FROM products WHERE kind = ? ORDER BY product_id", (kind,))
        return self.q("SELECT * FROM products ORDER BY product_id")

    def delinquent_customers(self) -> list[dict]:
        return self.q(
            "SELECT c.customer_id, a.account_id, a.days_past_due, a.late_fee, a.balance "
            "FROM accounts a JOIN customers c USING (customer_id) "
            "WHERE a.days_past_due > 0 ORDER BY a.account_id"
        )

    def open_alerts(self) -> list[dict]:
        return self.q("SELECT * FROM aml_alerts WHERE status = 'open' ORDER BY created_at, alert_id")

    def pending_applications(self) -> list[dict]:
        return self.q(
            "SELECT * FROM loan_applications WHERE status = 'pending' ORDER BY submitted_at, application_id"
        )

    def open_discrepancies(self) -> list[dict]:
        return self.q("SELECT * FROM ledger_entries WHERE status = 'discrepancy' ORDER BY entry_id")

    def counts(self) -> dict[str, int]:
        out = {}
        for t in (
            "customers", "accounts", "transactions", "beneficiaries", "cards", "disputes",
            "aml_alerts", "loan_applications", "ledger_entries", "access_grants", "messages", "products",
        ):
            out[t] = self.q1(f"SELECT COUNT(*) AS n FROM {t}")["n"]  # noqa: S608 - fixed table names
        return out


def row_value(row: dict | None, key: str, default: Any = None) -> Any:
    return default if row is None else row.get(key, default)
