"""SQLite store of validated log events, read by agent and by session.

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

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,               -- agent_id:session_id:step
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
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_agent ON events (agent_id, timestamp);
CREATE INDEX IF NOT EXISTS events_session ON events (session_id, step);
"""

FIELDS = (
    "agent_id", "agent_type", "session_id", "step", "timestamp", "action", "params",
    "auth_level", "policy_check", "outcome", "reasoning_summary",
)  # the schema's fields, minus the label

# events of a session stay together, sessions in the order they started
ORDER = "ORDER BY MIN(timestamp) OVER (PARTITION BY agent_id, session_id), agent_id, session_id, step"

COUNTS = (
    "COUNT(DISTINCT session_id) AS sessions, COUNT(*) AS events, "
    "SUM(policy_check = 'skipped') AS policy_skipped, SUM(policy_check = 'failed') AS policy_failed, "
    "SUM(outcome = 'blocked') AS blocked, SUM(outcome = 'error') AS errors, "
    "SUM(auth_level = 'elevated') AS elevated, MIN(timestamp) AS first_seen, MAX(timestamp) AS last_seen"
)


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

    def add(
        self,
        events: list[dict],
        *,
        source: str,
        otel_ids: list[tuple[str, str]] | None = None,
    ) -> list[str]:
        """Store validated events. The same agent, session and step replaces the
        earlier copy, so exporter retries and re-sent logs are stored once."""
        now = datetime.now(tz=UTC).isoformat()
        ids, rows = [], []
        for i, e in enumerate(events):
            trace_id, span_id = otel_ids[i] if otel_ids else (None, None)
            event_id = f"{e['agent_id']}:{e['session_id']}:{e['step']}"
            ids.append(event_id)
            rows.append((
                event_id, *(e[f] for f in FIELDS[:6]), json.dumps(e["params"]),
                *(e[f] for f in FIELDS[7:]), int(e[LABEL_FIELD]), source, trace_id, span_id, now,
            ))
        with self._lock, self._db:
            self._db.executemany(
                f"INSERT OR REPLACE INTO events (id, {', '.join(FIELDS)}, {LABEL_FIELD}, "
                f"source, otel_trace_id, otel_span_id, received_at) "
                f"VALUES ({', '.join('?' * 17)})",
                rows,
            )
        return ids

    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def events(
        self,
        *,
        agent_id: str | None = None,
        agent_type: str | None = None,
        session_id: str | None = None,
        action: str | None = None,
        limit: int = -1,
        offset: int = 0,
    ) -> list[dict]:
        """Events without their label, each session's together and in step order."""
        filters = {"agent_id": agent_id, "agent_type": agent_type, "session_id": session_id, "action": action}
        where = [(f"{k} = ?", v) for k, v in filters.items() if v is not None]
        sql = f"SELECT {', '.join(FIELDS)} FROM events"
        if where:
            sql += " WHERE " + " AND ".join(w for w, _ in where)
        sql += f" {ORDER} LIMIT ? OFFSET ?"
        rows = self._rows(sql, (*(v for _, v in where), limit, offset))
        return [{**dict(r), "params": json.loads(r["params"])} for r in rows]

    def agents(self) -> list[dict]:
        rows = self._rows(
            f"SELECT agent_id, MAX(agent_type) AS agent_type, {COUNTS} "
            "FROM events GROUP BY agent_id ORDER BY last_seen DESC"
        )
        return [_counts(r) for r in rows]

    def agent(self, agent_id: str) -> dict | None:
        rows = self._rows(
            f"SELECT agent_id, MAX(agent_type) AS agent_type, {COUNTS} "
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
                    "session_id": session_id,
                    "started": session[0]["timestamp"],
                    "ended": session[-1]["timestamp"],
                    "actions": [e["action"] for e in session],
                    "policy_skipped": sum(e["policy_check"] == "skipped" for e in session),
                }
                for session_id, session in by_session(events).items()
            ],
            "actions": dict(Counter(e["action"] for e in events).most_common()),
        }

    def labels(self, agent_id: str | None = None) -> dict[str, bool]:
        """session_id -> pressure_mode, for evaluation only."""
        sql = f"SELECT session_id, MAX({LABEL_FIELD}) AS label FROM events"
        args: tuple = ()
        if agent_id is not None:
            sql += " WHERE agent_id = ?"
            args = (agent_id,)
        rows = self._rows(sql + " GROUP BY session_id ORDER BY MIN(timestamp)", args)
        return {r["session_id"]: bool(r["label"]) for r in rows}


def by_session(events: list[dict]) -> dict[str, list[dict]]:
    """Events grouped by session, sessions in the order they come."""
    sessions: dict[str, list[dict]] = {}
    for e in events:
        sessions.setdefault(e["session_id"], []).append(e)
    return sessions


_TEXT = {"agent_id", "agent_type", "first_seen", "last_seen"}


def _counts(r: sqlite3.Row) -> dict:
    """An agent's summary row; SUM over no rows is NULL, so counts default to 0."""
    return {k: r[k] if k in _TEXT else (r[k] or 0) for k in r.keys()}
