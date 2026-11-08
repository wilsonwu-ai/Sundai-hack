"""Live scoring: score every session as its actions arrive.

Ingest stores the events and hands the touched sessions here; a small thread
pool re-scores each one (step k from steps 1..k, rankings cached, so earlier
steps cost nothing) and writes the step scores. Ingest never waits on
BehaviorGPT. Re-scoring the whole session also covers spans that arrive out
of order.
"""

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait

from risk.store import Store

log = logging.getLogger("risk.live")


class LiveScorer:
    def __init__(self, store: Store, detector, *, workers: int = 2):
        self.store = store
        self.detector = detector
        self.name = getattr(detector, "name", type(detector).__name__)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="live-score")
        self._lock = threading.Lock()
        self._queued: set[tuple[str, str, str]] = set()
        self._futures: set[Future] = set()

    def submit(self, sessions) -> None:
        """Queue (tenant, agent_id, session_id) keys; one already queued is not queued twice."""
        with self._lock:
            for key in sessions:
                if key in self._queued:
                    continue
                self._queued.add(key)
                future = self._pool.submit(self._score, key)
                self._futures.add(future)
                future.add_done_callback(self._futures.discard)

    def _score(self, key: tuple[str, str, str]) -> None:
        with self._lock:
            self._queued.discard(key)  # actions arriving from now on queue a fresh pass
        tenant, agent_id, session_id = key
        try:
            events = self.store.events(tenant=tenant, agent_id=agent_id, session_id=session_id)
            if events:
                result = self.detector.score_session(events)
                changed = self.store.save_scores(events, result["steps"], self.name)
                # auto-pause: every step decided review or pause goes to the review
                # queue (a step the gate already held keeps its hold)
                for event, step in zip(events, result["steps"]):
                    if step["decision"] in ("review", "pause"):
                        self.store.hold(event, step, source="observed", tenant=tenant)
                if changed and result["decision"] in ("review", "pause"):
                    top = max(result["steps"], key=lambda s: s["risk"])
                    log.warning("%s %s %s step %s: %s", tenant, agent_id, session_id, top["step"], top["decision"])
        except Exception:
            log.exception("scoring %s %s %s failed", tenant, agent_id, session_id)

    def drain(self, timeout: float | None = 30) -> None:
        """Wait for queued scoring to finish (tests, shutdown)."""
        with self._lock:
            pending = list(self._futures)
        wait(pending, timeout=timeout)
