"""SQLite store of validated log events, read by tenant, agent and session.

A tenant is a customer organisation running its own deployment of the agents:
the same agent code (agent_type, even the same agent_id) can serve several
tenants, and their events, sessions and scores are kept apart. Events with no
tenant belong to "default".

`pressure_mode` is kept in its own column for evaluation and never returned
with events, so nothing reading events can use the label as a feature.
"""

import json
import sqlite3
import threading
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from risk.ingest.load_logs import LABEL_FIELD

DEFAULT_TENANT = "default"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,               -- [tenant/]agent_id:session_id:step
    agent_id TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    timestamp TEXT NOT NULL,           -- UTC, fixed width
    action TEXT NOT NULL,
    params TEXT NOT NULL,              -- JSON
    auth_level TEXT NOT NULL,
    policy_check TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reasoning_summary TEXT NOT NULL,
    pressure_mode INTEGER NOT NULL,    -- ground-truth label: never returned with events
    source TEXT NOT NULL,              -- otel | api
    otel_trace_id TEXT,
    otel_span_id TEXT,
    received_at TEXT NOT NULL,
    tenant TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS events_agent ON events (agent_id, timestamp);
CREATE INDEX IF NOT EXISTS events_session ON events (session_id, step);

-- the live scorer's output: one row per scored step. A row is rewritten only
-- when its score changes, and a rewrite gets a new seq, so `seq > n` is
-- exactly what changed since a reader last looked.
CREATE TABLE IF NOT EXISTS step_scores (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    p REAL,
    expected TEXT,
    p_expected REAL,
    surprise REAL NOT NULL,
    severity REAL NOT NULL,
    risk REAL NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    flags TEXT NOT NULL,               -- JSON list of broken role controls
    customer_id TEXT,                  -- the account holder, from the event's params
    categories TEXT NOT NULL,          -- JSON list of violation categories
    exposure_usd REAL NOT NULL,        -- this step
    loss_if_harmful_usd REAL NOT NULL, -- this step, after recovery
    at_stake_usd REAL NOT NULL,        -- the session through this step
    p_unsafe REAL NOT NULL,            -- the session through this step
    expected_loss_usd REAL NOT NULL,   -- the session through this step
    detector TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    UNIQUE (tenant, agent_id, session_id, step)
);
CREATE INDEX IF NOT EXISTS scores_decision ON step_scores (decision, seq);

-- the review queue: risky steps held for a person. source "gate" means the
-- agent asked first and is waiting; "observed" means it already ran and a
-- person confirms or escalates. One hold per step; a decision is final.
CREATE TABLE IF NOT EXISTS holds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    agent_type TEXT NOT NULL,
    session_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    action TEXT NOT NULL,
    source TEXT NOT NULL,              -- gate | observed
    decision TEXT NOT NULL,            -- pause | review: what the monitor decided
    risk REAL NOT NULL,
    reason TEXT NOT NULL,
    categories TEXT NOT NULL,          -- JSON list
    loss_if_harmful_usd REAL NOT NULL,
    at_stake_usd REAL NOT NULL,
    expected_loss_usd REAL NOT NULL,
    event TEXT NOT NULL,               -- JSON: the action as proposed or observed, label removed
    status TEXT NOT NULL,              -- pending | approved | rejected
    reviewer TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    UNIQUE (tenant, agent_id, session_id, step)
);
CREATE INDEX IF NOT EXISTS holds_status ON holds (status, id);
"""

HOLD_STATUSES = ("pending", "approved", "rejected")

SCORE_FIELDS = (
    "tenant", "agent_id", "agent_type", "session_id", "step", "timestamp", "action", "p", "expected",
    "p_expected", "surprise", "severity", "risk", "decision", "reason", "flags", "customer_id",
    "categories", "exposure_usd", "loss_if_harmful_usd", "at_stake_usd", "p_unsafe",
    "expected_loss_usd", "detector", "scored_at",
)
STEP_KEYS = SCORE_FIELDS[6:-2]  # what a scored step dict carries, from action to expected loss

FIELDS = (
    "agent_id", "agent_type", "session_id", "step", "timestamp", "action", "params",
    "auth_level", "policy_check", "outcome", "reasoning_summary",
)  # the schema's fields, minus the label

# events of a session stay together, sessions in the order they started
ORDER = (
    "ORDER BY MIN(timestamp) OVER (PARTITION BY tenant, agent_id, session_id), "
    "tenant, agent_id, session_id, step"
)

COUNTS = (
    "COUNT(DISTINCT tenant || '/' || session_id) AS sessions, COUNT(*) AS events, "
    "SUM(policy_check = 'skipped') AS policy_skipped, SUM(policy_check = 'failed') AS policy_failed, "
    "SUM(outcome = 'blocked') AS blocked, SUM(outcome = 'error') AS errors, "
    "SUM(auth_level = 'elevated') AS elevated, MIN(timestamp) AS first_seen, MAX(timestamp) AS last_seen"
)


def event_id(tenant: str, e: dict) -> str:
    base = f"{e['agent_id']}:{e['session_id']}:{e['step']}"
    return base if tenant == DEFAULT_TENANT else f"{tenant}/{base}"


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            # events from before tenants existed belong to the default tenant
            if "tenant" not in self._columns("events"):
                self._db.execute(f"ALTER TABLE events ADD COLUMN tenant TEXT NOT NULL DEFAULT '{DEFAULT_TENANT}'")
            self._db.execute("CREATE INDEX IF NOT EXISTS events_tenant ON events (tenant, agent_id, session_id)")
            # step scores are derived: an older layout is dropped, and the live
            # scorer rebuilds it from the events on start
            if not set(SCORE_FIELDS) <= self._columns("step_scores"):
                self._db.execute("DROP TABLE step_scores")
                self._db.executescript(SCHEMA)
            self._db.commit()

    def _columns(self, table: str) -> set[str]:
        return {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}

    def add(
        self,
        events: list[dict],
        *,
        source: str,
        otel_ids: list[tuple[str, str]] | None = None,
        tenants: list[str] | None = None,
    ) -> list[str]:
        """Store validated events, each under its tenant. The same tenant, agent,
        session and step replaces the earlier copy, so exporter retries and
        re-sent logs are stored once."""
        now = datetime.now(tz=UTC).isoformat()
        ids, rows = [], []
        for i, e in enumerate(events):
            trace_id, span_id = otel_ids[i] if otel_ids else (None, None)
            tenant = tenants[i] if tenants else DEFAULT_TENANT
            eid = event_id(tenant, e)
            ids.append(eid)
            rows.append((
                eid, *(e[f] for f in FIELDS[:6]), json.dumps(e["params"]),
                *(e[f] for f in FIELDS[7:]), int(e[LABEL_FIELD]), source, trace_id, span_id, now, tenant,
            ))
        with self._lock, self._db:
            self._db.executemany(
                f"INSERT OR REPLACE INTO events (id, {', '.join(FIELDS)}, {LABEL_FIELD}, "
                f"source, otel_trace_id, otel_span_id, received_at, tenant) "
                f"VALUES ({', '.join('?' * 18)})",
                rows,
            )
        return ids

    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def events(
        self,
        *,
        tenant: str | None = None,
        agent_id: str | None = None,
        agent_type: str | None = None,
        session_id: str | None = None,
        action: str | None = None,
        limit: int = -1,
        offset: int = 0,
    ) -> list[dict]:
        """Events without their label, each with its tenant, each session's
        together and in step order."""
        filters = {"tenant": tenant, "agent_id": agent_id, "agent_type": agent_type,
                   "session_id": session_id, "action": action}
        where = [(f"{k} = ?", v) for k, v in filters.items() if v is not None]
        sql = f"SELECT tenant, {', '.join(FIELDS)} FROM events"
        if where:
            sql += " WHERE " + " AND ".join(w for w, _ in where)
        sql += f" {ORDER} LIMIT ? OFFSET ?"
        rows = self._rows(sql, (*(v for _, v in where), limit, offset))
        return [{**dict(r), "params": json.loads(r["params"])} for r in rows]

    def agents(self) -> list[dict]:
        rows = self._rows(
            f"SELECT agent_id, MAX(agent_type) AS agent_type, COUNT(DISTINCT tenant) AS tenants, {COUNTS} "
            "FROM events GROUP BY agent_id ORDER BY last_seen DESC"
        )
        return [_counts(r) for r in rows]

    def agent(self, agent_id: str) -> dict | None:
        rows = self._rows(
            f"SELECT agent_id, MAX(agent_type) AS agent_type, COUNT(DISTINCT tenant) AS tenants, {COUNTS} "
            "FROM events WHERE agent_id = ? GROUP BY agent_id",
            (agent_id,),
        )
        if not rows:
            return None
        events = self.events(agent_id=agent_id)
        return {
            **_counts(rows[0]),
            "session_list": [
                {
                    "session_id": session[0]["session_id"],
                    "tenant": session[0]["tenant"],
                    "started": session[0]["timestamp"],
                    "ended": session[-1]["timestamp"],
                    "actions": [e["action"] for e in session],
                    "policy_skipped": sum(e["policy_check"] == "skipped" for e in session),
                }
                for session in by_session(events).values()
            ],
            "actions": dict(Counter(e["action"] for e in events).most_common()),
        }

    def sessions(self) -> list[tuple[str, str, str]]:
        """Every (tenant, agent_id, session_id) with events."""
        rows = self._rows("SELECT DISTINCT tenant, agent_id, session_id FROM events")
        return [(r["tenant"], r["agent_id"], r["session_id"]) for r in rows]

    def tenants(self) -> list[dict]:
        rows = self._rows(
            "SELECT tenant, COUNT(DISTINCT agent_id) AS agents, COUNT(DISTINCT agent_id || '/' || session_id) AS sessions, "
            "COUNT(*) AS events, MAX(timestamp) AS last_seen FROM events GROUP BY tenant ORDER BY tenant"
        )
        return [dict(r) for r in rows]

    # -- live scores ------------------------------------------------------------

    def save_scores(self, events: list[dict], steps: list[dict], detector: str) -> int:
        """Store a session's step scores; only changed rows are rewritten. Returns how many."""
        if not events:
            return 0
        tenant = events[0].get("tenant", DEFAULT_TENANT)
        agent_id, session_id = events[0]["agent_id"], events[0]["session_id"]
        now = datetime.now(tz=UTC).isoformat()

        def row_key(action, risk, decision, expected_loss, det):
            return (action, risk, decision, round(expected_loss, 2), det)

        with self._lock, self._db:
            current = {
                r["step"]: row_key(r["action"], r["risk"], r["decision"], r["expected_loss_usd"], r["detector"])
                for r in self._db.execute(
                    "SELECT step, action, risk, decision, expected_loss_usd, detector FROM step_scores "
                    "WHERE tenant = ? AND agent_id = ? AND session_id = ?",
                    (tenant, agent_id, session_id),
                )
            }
            rows = [
                (
                    tenant, agent_id, e["agent_type"], session_id, s["step"], e["timestamp"],
                    *(json.dumps(s.get(k, [])) if k in ("flags", "categories") else s.get(k) for k in STEP_KEYS),
                    detector, now,
                )
                for e, s in zip(events, steps)
                if current.get(s["step"])
                != row_key(s["action"], s["risk"], s["decision"], s.get("expected_loss_usd", 0.0), detector)
            ]
            self._db.executemany(
                f"INSERT OR REPLACE INTO step_scores ({', '.join(SCORE_FIELDS)}) "
                f"VALUES ({', '.join('?' * len(SCORE_FIELDS))})",
                rows,
            )
        return len(rows)

    def scores_since(self, seq: int = 0, limit: int = 1000) -> list[dict]:
        """Step scores written after `seq`, oldest first: the live feed."""
        rows = self._rows(
            f"SELECT seq, {', '.join(SCORE_FIELDS)} FROM step_scores WHERE seq > ? ORDER BY seq LIMIT ?",
            (seq, limit),
        )
        return [_score_row(r) for r in rows]

    def session_scores(self, session_id: str, tenant: str | None = None) -> list[dict]:
        sql = f"SELECT seq, {', '.join(SCORE_FIELDS)} FROM step_scores WHERE session_id = ?"
        args: tuple = (session_id,)
        if tenant is not None:
            sql += " AND tenant = ?"
            args += (tenant,)
        return [_score_row(r) for r in self._rows(sql + " ORDER BY tenant, agent_id, step", args)]

    def alerts(self, limit: int = 50) -> list[dict]:
        """Steps decided review or pause, newest first."""
        rows = self._rows(
            f"SELECT seq, {', '.join(SCORE_FIELDS)} FROM step_scores "
            "WHERE decision IN ('review', 'pause') ORDER BY seq DESC LIMIT ?",
            (limit,),
        )
        return [_score_row(r) for r in rows]

    def latest_seq(self) -> int:
        return self._rows("SELECT COALESCE(MAX(seq), 0) AS seq FROM step_scores")[0]["seq"]

    # -- review queue ------------------------------------------------------------

    def hold(self, event: dict, step: dict, *, source: str, tenant: str = DEFAULT_TENANT) -> tuple[dict, bool]:
        """Queue a risky step for a person, unless that step already has a hold.
        Returns (the hold, whether it is new)."""
        now = datetime.now(tz=UTC).isoformat()
        clean = {k: v for k, v in event.items() if k not in (LABEL_FIELD, "tenant")}
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO holds (tenant, agent_id, agent_type, session_id, step, action, source, "
                "decision, risk, reason, categories, loss_if_harmful_usd, at_stake_usd, expected_loss_usd, "
                "event, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (
                    tenant, event["agent_id"], event["agent_type"], event["session_id"], event["step"],
                    event["action"], source, step["decision"], step["risk"], step["reason"],
                    json.dumps(step.get("categories", [])), step.get("loss_if_harmful_usd", 0.0),
                    step.get("at_stake_usd", 0.0), step.get("expected_loss_usd", 0.0), json.dumps(clean), now,
                ),
            )
            created = cur.rowcount == 1
            row = self._db.execute(
                "SELECT * FROM holds WHERE tenant = ? AND agent_id = ? AND session_id = ? AND step = ?",
                (tenant, event["agent_id"], event["session_id"], event["step"]),
            ).fetchone()
        return _hold_row(row), created

    def holds(self, *, status: str | None = None, tenant: str | None = None, limit: int = 500) -> list[dict]:
        """Held steps, pending first, newest first within each status."""
        where, args = [], []
        if status is not None:
            where.append("status = ?")
            args.append(status)
        if tenant is not None:
            where.append("tenant = ?")
            args.append(tenant)
        sql = "SELECT * FROM holds" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY status = 'pending' DESC, id DESC LIMIT ?"
        return [_hold_row(r) for r in self._rows(sql, (*args, limit))]

    def get_hold(self, hold_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM holds WHERE id = ?", (hold_id,))
        return _hold_row(rows[0]) if rows else None

    def decide(self, hold_id: int, status: str, reviewer: str, note: str | None) -> dict | None:
        """Record a person's decision on a pending hold. Returns None if there is no
        such hold; raises ValueError if it was already decided."""
        if status not in ("approved", "rejected"):
            raise ValueError(f"status must be approved or rejected, not {status!r}")
        now = datetime.now(tz=UTC).isoformat()
        with self._lock, self._db:
            row = self._db.execute("SELECT status FROM holds WHERE id = ?", (hold_id,)).fetchone()
            if row is None:
                return None
            if row["status"] != "pending":
                raise ValueError(f"hold {hold_id} was already {row['status']}")
            self._db.execute(
                "UPDATE holds SET status = ?, reviewer = ?, note = ?, decided_at = ? WHERE id = ?",
                (status, reviewer, note, now, hold_id),
            )
        return self.get_hold(hold_id)

    def labels(self, agent_id: str | None = None) -> dict[str, bool]:
        """session_id -> pressure_mode, for evaluation only."""
        sql = f"SELECT session_id, MAX({LABEL_FIELD}) AS label FROM events"
        args: tuple = ()
        if agent_id is not None:
            sql += " WHERE agent_id = ?"
            args = (agent_id,)
        rows = self._rows(sql + " GROUP BY session_id ORDER BY MIN(timestamp)", args)
        return {r["session_id"]: bool(r["label"]) for r in rows}


def session_key(e: dict) -> str:
    """A session's key: its id, prefixed by its tenant unless that is the default."""
    tenant = e.get("tenant", DEFAULT_TENANT)
    return e["session_id"] if tenant == DEFAULT_TENANT else f"{tenant}/{e['session_id']}"


def by_session(events: list[dict]) -> dict[str, list[dict]]:
    """Events grouped by session (per tenant), sessions in the order they come."""
    sessions: dict[str, list[dict]] = {}
    for e in events:
        sessions.setdefault(session_key(e), []).append(e)
    return sessions


_TEXT = {"agent_id", "agent_type", "first_seen", "last_seen"}


def _hold_row(r: sqlite3.Row) -> dict:
    return {**dict(r), "categories": json.loads(r["categories"]), "event": json.loads(r["event"])}


def _score_row(r: sqlite3.Row) -> dict:
    return {**dict(r), "flags": json.loads(r["flags"]), "categories": json.loads(r["categories"])}


def _counts(r: sqlite3.Row) -> dict:
    """An agent's summary row; SUM over no rows is NULL, so counts default to 0."""
    return {k: r[k] if k in _TEXT else (r[k] or 0) for k in r.keys()}
