"""OpenTelemetry spans, shaped like risk/ingest/replay_otel.py: trace per session, span per action.

Span name = the action; attributes = every log event field (`params` as a JSON string). Each session
also gets a root span named "session <id>" with session_id, agent_id and agent_type. Span times are the
simulated clock, so a run is reproducible. Sent over OTLP/HTTP to OTEL_EXPORTER_OTLP_ENDPOINT
(default http://localhost:4318). If the collector is down, the run carries on and drops the spans.
"""

from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime

from bank.tools.types import Session

log = logging.getLogger("bank.otel")
MS = 1_000_000


def to_ns(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp).timestamp() * 1e9)


def span_attributes(event: dict) -> dict:
    return {k: json.dumps(v) if k == "params" else v for k, v in event.items()}


class Telemetry:
    """No-op unless enabled."""

    def __init__(self, enabled: bool, endpoint: str | None = None, seed: int = 0):
        self.enabled = enabled
        self.endpoint = (endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "http://localhost:4318").rstrip("/")
        self.failures = 0
        self._provider = None
        self._tracer = None
        if not enabled:
            return
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
        from opentelemetry.sdk.trace.id_generator import IdGenerator

        outer = self

        class SeededIds(IdGenerator):           # reproducible trace/span ids for a given seed
            def __init__(self) -> None:
                self.rng = random.Random(f"{seed}:otel-ids")

            def generate_span_id(self) -> int:
                return self.rng.getrandbits(64) or 1

            def generate_trace_id(self) -> int:
                return self.rng.getrandbits(128) or 1

        class SafeExporter(SpanExporter):       # a dead collector must never stop the simulation
            def __init__(self) -> None:
                self.inner = OTLPSpanExporter(endpoint=f"{outer.endpoint}/v1/traces", timeout=2)

            def export(self, spans):
                try:
                    result = self.inner.export(spans)
                except Exception as exc:  # noqa: BLE001
                    result = SpanExportResult.FAILURE
                    log.debug("span export raised %s", exc)
                if result != SpanExportResult.SUCCESS:
                    outer.failures += 1
                    if outer.failures == 1:
                        log.warning("OTLP collector at %s is not accepting spans; carrying on without it", outer.endpoint)
                else:
                    outer.failures = 0
                return result

            def shutdown(self) -> None:
                self.inner.shutdown()

            def force_flush(self, timeout_millis: int = 30000) -> bool:
                return True

        self._provider = TracerProvider(resource=Resource.create({"service.name": "sundai-bank-agents"}),
                                        id_generator=SeededIds())
        self._provider.add_span_processor(BatchSpanProcessor(SafeExporter(), schedule_delay_millis=250))
        self._tracer = self._provider.get_tracer("bank.tools.telemetry")
        logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").setLevel(logging.CRITICAL)

    def start_session(self, s: Session, ts: str) -> None:
        if not self._tracer:
            return
        s.span = self._tracer.start_span(
            f"session {s.session_id}", start_time=to_ns(ts),
            attributes={"session_id": s.session_id, "agent_id": s.agent_id, "agent_type": s.agent_type})

    def emit(self, s: Session, event: dict) -> None:
        if not self._tracer or s.span is None:
            return
        from opentelemetry import trace
        ctx = trace.set_span_in_context(s.span)
        start = to_ns(event["timestamp"])
        span = self._tracer.start_span(event["action"], context=ctx, start_time=start, attributes=span_attributes(event))
        span.end(end_time=start + MS)

    def end_session(self, s: Session) -> None:
        if s.span is not None and s.last_ts:
            s.span.end(end_time=to_ns(s.last_ts) + MS)
            s.span = None

    def flush(self, force: bool = False) -> None:
        """Push pending spans out now (the gate wants the monitor to have seen the session so far)."""
        if self._provider and (force or self.failures == 0):
            self._provider.force_flush(timeout_millis=2000)

    def shutdown(self) -> None:
        if self._provider:
            self._provider.shutdown()
            self._provider = None
