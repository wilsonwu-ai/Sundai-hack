"""Replay .jsonl logs to the monitor as OpenTelemetry spans, as the bank's agents emit them.

    python -m risk.ingest.replay_otel                                  # shared/sample_logs
    python -m risk.ingest.replay_otel path/to/logs --endpoint http://host:4318
    python -m risk.ingest.replay_otel --delay 1.5                      # live: one action at a time
    python -m risk.ingest.replay_otel --tenants "Northgate Bank,Harbor Credit Union"
                                      # the same agents serving several customer orgs
    python -m risk.ingest.replay_otel --delay 1 --gate
                                      # ask the gate first; held actions wait for a person

Also the reference for the agent team's emitter: one trace per session, one
span per action named after it, every log event field as a span attribute,
`params` as a JSON string (OTel attributes can't nest). The customer org a
deployment serves goes in `tenant.id` (here on each span; one org per process
would put it on the resource).
"""

import argparse
import json
import time
from collections import defaultdict
from collections.abc import Callable
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


def assign_tenants(sessions: dict[str, list[dict]], tenants: list[str] | None) -> dict[str, str | None]:
    """Spread each agent's sessions over the tenants, so every org runs every agent;
    each agent starts at a different org, so no org gets all the bad sessions."""
    if not tenants:
        return {sid: None for sid in sessions}
    by_agent: dict[str, list[str]] = defaultdict(list)
    for sid, evs in sorted(sessions.items(), key=lambda kv: min(e["timestamp"] for e in kv[1])):
        by_agent[evs[0]["agent_id"]].append(sid)
    out = {}
    for offset, (_, sids) in enumerate(sorted(by_agent.items())):
        for i, sid in enumerate(sids):
            out[sid] = tenants[(i + offset) % len(tenants)]
    return out


def emit_session(tracer: trace.Tracer, events: list[dict], tenant: str | None = None) -> None:
    """One session as one trace: a root span, then one child span per action."""
    events = sorted(events, key=lambda e: e["step"])
    first, last = events[0], events[-1]
    org = {"tenant.id": tenant} if tenant else {}
    root = tracer.start_span(
        f"session {first['session_id']}",
        start_time=to_ns(first["timestamp"]),
        attributes={**{k: first[k] for k in ("session_id", "agent_id", "agent_type")}, **org},
    )
    ctx = trace.set_span_in_context(root)
    for e in events:
        start = to_ns(e["timestamp"])
        span = tracer.start_span(e["action"], context=ctx, start_time=start, attributes={**span_attributes(e), **org})
        span.end(end_time=start + MS)
    root.end(end_time=to_ns(last["timestamp"]) + MS)


def replay(folder, exporter: SpanExporter, tenants: list[str] | None = None) -> int:
    """Send every session in `folder` through `exporter`; returns the number of sessions."""
    sessions = defaultdict(list)
    for event in read_events(folder):
        sessions[event["session_id"]].append(event)
    provider = TracerProvider(resource=Resource.create({"service.name": "sundai-bank-replay"}))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    tracer = provider.get_tracer("risk.ingest.replay_otel")
    orgs = assign_tenants(sessions, tenants)
    for sid, events in sessions.items():
        emit_session(tracer, events, orgs[sid])
    provider.shutdown()  # flushes
    return len(sessions)


def replay_live(
    folder,
    exporter: SpanExporter,
    delay: float,
    say=print,
    tenants: list[str] | None = None,
    gate: Callable[[dict, str | None], dict] | None = None,
    hold_status: Callable[[int], str] | None = None,
    wait: float = 300.0,
) -> int:
    """Send one action every `delay` seconds, the sessions interleaved, each span
    exported as soon as it ends: what the monitor sees from agents working at once.

    With `gate`, each action asks first. A held action suspends its session (the
    others carry on) until `hold_status` says a person approved it, then it runs;
    rejected, the session stops there. Gives up after `wait` seconds with only
    held sessions left."""
    sessions = defaultdict(list)
    for event in read_events(folder):
        sessions[event["session_id"]].append(event)
    orgs = assign_tenants(sessions, tenants)
    runs = {sid: sorted(evs, key=lambda e: e["step"]) for sid, evs in sessions.items()}
    provider = TracerProvider(resource=Resource.create({"service.name": "sundai-bank-replay"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("risk.ingest.replay_otel")
    roots: dict[str, trace.Span] = {}
    pos = {sid: 0 for sid in runs}
    held: dict[str, int] = {}
    done: set[str] = set()

    def finish(sid):
        if sid in roots:
            roots.pop(sid).end()
        done.add(sid)

    def run_step(sid):
        e = runs[sid][pos[sid]]
        org = {"tenant.id": orgs[sid]} if orgs[sid] else {}
        if sid not in roots:
            roots[sid] = tracer.start_span(
                f"session {sid}", attributes={**{k: e[k] for k in ("session_id", "agent_id", "agent_type")}, **org}
            )
        span = tracer.start_span(
            e["action"], context=trace.set_span_in_context(roots[sid]), attributes={**span_attributes(e), **org}
        )
        span.end()
        say(f"-> {orgs[sid] or 'default':<22} {e['agent_id']:<12} {sid}  step {e['step']}  {e['action']}")
        pos[sid] += 1
        if pos[sid] == len(runs[sid]):
            finish(sid)

    idle_since = None
    while len(done) < len(runs):
        progressed = False
        for sid in runs:
            if sid in done:
                continue
            e = runs[sid][pos[sid]]
            if sid in held:
                status = hold_status(held[sid])
                if status == "pending":
                    continue
                del held[sid]
                if status == "approved":
                    say(f"   approved by a person: {sid} step {e['step']} {e['action']} runs")
                    run_step(sid)
                else:
                    say(f"   rejected by a person: {sid} step {e['step']} {e['action']} never runs; the agent stops")
                    finish(sid)
            elif gate is not None and not (verdict := gate(e, orgs[sid])).get("allowed", True):
                held[sid] = verdict["hold_id"]
                say(f"|| held {orgs[sid] or 'default':<19} {e['agent_id']:<12} {sid}  step {e['step']}  {e['action']}: "
                    f"waiting for a person (hold {verdict['hold_id']})")
            else:
                run_step(sid)
            progressed = True
            time.sleep(delay)
        if progressed:
            idle_since = None
        elif held:
            idle_since = idle_since or time.time()
            if time.time() - idle_since > wait:
                say(f"gave up after {wait:.0f}s with {len(held)} session(s) still waiting for a person")
                for sid in list(held):
                    finish(sid)
                break
            time.sleep(1.0)
    provider.shutdown()
    return len(runs)


def main() -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", nargs="?", default=DEFAULT_LOG_DIR)
    parser.add_argument("--endpoint", default="http://localhost:4318")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds between actions: replay live")
    parser.add_argument("--tenants", default="", help="comma-separated customer orgs to spread the sessions over")
    parser.add_argument("--gate", action="store_true", help="ask POST /gate before each action; held ones wait for a person")
    parser.add_argument("--wait", type=float, default=300.0, help="with --gate: seconds to wait for people before giving up")
    args = parser.parse_args()
    endpoint = args.endpoint.rstrip("/")
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    tenants = [t.strip() for t in args.tenants.split(",") if t.strip()] or None
    if args.gate:
        import httpx

        api = httpx.Client(base_url=endpoint, timeout=30)

        def gate(event, org):
            r = api.post("/gate", params={"tenant": org or "default"}, json=event)
            r.raise_for_status()
            return r.json()

        def hold_status(hold_id):
            return api.get(f"/holds/{hold_id}").json()["status"]

        n = replay_live(args.folder, exporter, args.delay, tenants=tenants, gate=gate, hold_status=hold_status, wait=args.wait)
    elif args.delay > 0:
        n = replay_live(args.folder, exporter, args.delay, tenants=tenants)
    else:
        n = replay(args.folder, exporter, tenants)
    print(f"replayed {n} sessions; see {endpoint}/dashboard")


if __name__ == "__main__":
    main()
