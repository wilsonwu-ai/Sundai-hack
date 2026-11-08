"""BehaviorGPT's ranking of the action catalog after a history, cached and retried.

Scoring step k of a session needs the ranking after steps 1..k-1, and the same
prefixes come back every time the monitor re-scores an agent, so every ranking
is cached on disk by (catalog, history).
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

import httpx
from behaviorgpt import UnboxAIClient, UnboxAIError, View

from risk.ingest.load_logs import REPO_ROOT

DEFAULT_CACHE = REPO_ROOT / "risk" / "data" / "behaviorgpt_cache.sqlite"
RETRYABLE = {429, 500, 502, 503, 504}


class Ranker(Protocol):
    def scores_many(self, histories: list[tuple[str, ...]]) -> list[dict[str, float]]:
        """For each history (item ids, oldest first), item id -> ranking score."""
        ...


class Cache:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS ranks (k TEXT PRIMARY KEY, v TEXT)")
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, float] | None:
        with self._lock:
            row = self._db.execute("SELECT v FROM ranks WHERE k = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict[str, float]) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO ranks VALUES (?, ?)", (key, json.dumps(value)))


class BehaviorGPTRanker:
    def __init__(
        self,
        catalog_id: str | None = None,
        *,
        client: UnboxAIClient | None = None,
        cache_path: Path | None = DEFAULT_CACHE,
        limit: int = 50,
        retries: int = 5,
        workers: int = 8,
    ):
        self.catalog_id = catalog_id or os.environ["UNBOXAI_CATALOG_ID"]
        self.client = client or UnboxAIClient(market="us")
        self.cache = Cache(cache_path) if cache_path else None
        self.limit = limit
        self.retries = retries
        self.pool = ThreadPoolExecutor(max_workers=workers)

    def scores(self, history: tuple[str, ...]) -> dict[str, float]:
        key = hashlib.sha256(
            json.dumps([self.catalog_id, list(history), self.limit]).encode()
        ).hexdigest()
        if self.cache and (hit := self.cache.get(key)) is not None:
            return hit
        res = self._call(history)
        ranking = {item.id: float(item.score) for item in res.items}
        if self.cache:
            self.cache.put(key, ranking)
        return ranking

    def scores_many(self, histories: list[tuple[str, ...]]) -> list[dict[str, float]]:
        return list(self.pool.map(self.scores, histories))

    def _call(self, history: tuple[str, ...]):
        for attempt in range(self.retries + 1):
            try:
                return self.client.complete(
                    [View(i) for i in history],
                    limit=self.limit,
                    catalog_id=self.catalog_id,
                    register_event=False,  # a ranking for us, not an event of a user
                )
            except UnboxAIError as exc:
                if exc.status_code not in RETRYABLE or attempt == self.retries:
                    raise
            except httpx.TransportError:
                if attempt == self.retries:
                    raise
            time.sleep(min(2**attempt, 20))
        raise AssertionError("unreachable")
