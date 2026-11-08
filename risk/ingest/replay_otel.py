"""Replay .jsonl logs to the monitor as OpenTelemetry spans, as the bank's agents emit them.

    python -m risk.ingest.replay_otel                                  # shared/sample_logs
    python -m risk.ingest.replay_otel path/to/logs --endpoint http://host:4318
    python -m risk.ingest.replay_otel --delay 1.5                      # live: one action at a time

Also the reference for the agent team's emitter: one trace per session, one
span per action named after it, every log event field as a span attribute,
`params` as a JSON string (OTel attributes can't nest).
"""

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter

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


def replay_live(folder, exporter: SpanExporter, delay: float, say=print) -> int:
    """Send one action every `delay` seconds, the sessions interleaved, each span
    exported as soon as it ends: what the monitor sees from agents working at once."""
    sessions = defaultdict(list)
    for event in read_events(folder):
        sessions[event["session_id"]].append(event)
    runs = [sorted(evs, key=lambda e: e["step"]) for evs in sessions.values()]
    provider = TracerProvider(resource=Resource.create({"service.name": "sundai-bank-replay"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("risk.ingest.replay_otel")
    roots: dict[str, trace.Span] = {}
    for i in range(max(map(len, runs))):
        for run in runs:
            if i >= len(run):
                continue
            e = run[i]
            sid = e["session_id"]
            if sid not in roots:
                roots[sid] = tracer.start_span(
                    f"session {sid}", attributes={k: e[k] for k in ("session_id", "agent_id", "agent_type")}
                )
            span = tracer.start_span(
                e["action"], context=trace.set_span_in_context(roots[sid]), attributes=span_attributes(e)
            )
            span.end()
            if i == len(run) - 1:
                roots.pop(sid).end()
            say(f"-> {e['agent_id']:<12} {sid}  step {e['step']}  {e['action']}")
            time.sleep(delay)
    provider.shutdown()
    return len(runs)


def main() -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", nargs="?", default=DEFAULT_LOG_DIR)
    parser.add_argument("--endpoint", default="http://localhost:4318")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds between actions: replay live")
    args = parser.parse_args()
    endpoint = args.endpoint.rstrip("/")
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    if args.delay > 0:
        n = replay_live(args.folder, exporter, args.delay)
    else:
        n = replay(args.folder, exporter)
    print(f"replayed {n} sessions; see {endpoint}/dashboard")


if __name__ == "__main__":
    main()
