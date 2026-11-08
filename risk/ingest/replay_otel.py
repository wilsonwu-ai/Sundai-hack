"""Replay .jsonl logs to the monitor as OpenTelemetry spans, as the bank's agents emit them.

    python -m risk.ingest.replay_otel                                  # shared/sample_logs
    python -m risk.ingest.replay_otel path/to/logs --endpoint http://host:4318

Also the reference for the agent team's emitter: one trace per session, one
span per action named after it, every log event field as a span attribute,
`params` as a JSON string (OTel attributes can't nest).
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from risk.ingest.load_logs import DEFAULT_LOG_DIR, read_events

MS = 1_000_000


def to_ns(timestamp: str) -> int:
    return int(datetime.fromisoformat(timestamp).timestamp() * 1e9)


def span_attributes(event: dict) -> dict:
    return {k: json.dumps(v) if k == "params" else v for k, v in event.items()}


def emit_session(tracer: trace.Tracer, events: list[dict]) -> None:
    """One session as one trace: a root span, then one child span per action."""
    events = sorted(events, key=lambda e: e["step"])
    first, last = events[0], events[-1]
    root = tracer.start_span(
        f"session {first['session_id']}",
        start_time=to_ns(first["timestamp"]),
        attributes={k: first[k] for k in ("session_id", "agent_id", "agent_type")},
    )
    ctx = trace.set_span_in_context(root)
    for e in events:
        start = to_ns(e["timestamp"])
        span = tracer.start_span(e["action"], context=ctx, start_time=start, attributes=span_attributes(e))
        span.end(end_time=start + MS)
    root.end(end_time=to_ns(last["timestamp"]) + MS)


def replay(folder, exporter: SpanExporter) -> int:
    """Send every session in `folder` through `exporter`; returns the number of sessions."""
    sessions = defaultdict(list)
    for event in read_events(folder):
        sessions[event["session_id"]].append(event)
    provider = TracerProvider(resource=Resource.create({"service.name": "sundai-bank-replay"}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    tracer = provider.get_tracer("risk.ingest.replay_otel")
    for events in sessions.values():
        emit_session(tracer, events)
    provider.shutdown()  # flushes
    return len(sessions)


def main() -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", nargs="?", default=DEFAULT_LOG_DIR)
    parser.add_argument("--endpoint", default="http://localhost:4318")
    args = parser.parse_args()
    endpoint = args.endpoint.rstrip("/")
    n = replay(args.folder, OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    print(f"replayed {n} sessions; see {endpoint}/agents")


if __name__ == "__main__":
    main()
