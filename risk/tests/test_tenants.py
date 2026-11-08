"""Customer orgs (tenants): the same agent code serving several orgs, kept apart."""

import sqlite3

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from risk.api.app import create_app
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from risk.ingest.load_logs import DEFAULT_LOG_DIR, read_events
from risk.ingest.replay_otel import assign_tenants, replay, span_attributes
from risk.scoring.surprise_severity import SurpriseSeverityDetector
from risk.store import Store
from risk.tests.test_api import post_pb
from risk.tests.test_detector import FakeRanker

ORGS = ["Northgate Bank", "Harbor Credit Union"]


def live_client(tmp_path):
    app = create_app(tmp_path / "m.sqlite", detector=SurpriseSeverityDetector(FakeRanker()))
    return TestClient(app), app.state.live


def test_the_same_session_under_two_orgs_is_kept_apart(tmp_path):
    client, live = live_client(tmp_path)
    session = [e for e in read_events() if e["session_id"] == "s_002"]
    for org in ORGS:
        assert client.post("/events", params={"tenant": org}, json=session).status_code == 200
    live.drain()

    assert len(client.get("/events").json()) == 8  # 4 steps x 2 orgs, not overwritten
    assert {e["tenant"] for e in client.get("/events").json()} == set(ORGS)
    assert len(client.get("/events", params={"tenant": ORGS[0]}).json()) == 4
    assert len(client.get("/sessions/s_002", params={"tenant": ORGS[1]}).json()) == 4

    steps = client.get("/live/steps").json()["steps"]
    assert sorted({(s["tenant"], s["session_id"]) for s in steps}) == sorted((o, "s_002") for o in ORGS)
    assert len(client.get("/sessions/s_002/scores", params={"tenant": ORGS[0]}).json()) == 4

    tenants = {t["tenant"]: t for t in client.get("/tenants").json()}
    assert tenants[ORGS[0]]["agents"] == 1 and tenants[ORGS[0]]["events"] == 4
    # the agent's verdict covers both orgs' sessions separately
    risk = client.get("/agents/payments-01/risk").json()
    assert sorted(risk["sessions"]) == sorted(f"{o}/s_002" for o in ORGS)


def test_tenant_comes_from_the_span_or_its_resource(tmp_path):
    client, live = live_client(tmp_path)
    on_spans = InMemorySpanExporter()
    replay(DEFAULT_LOG_DIR, on_spans, tenants=ORGS)  # tenant.id on each span
    post_pb(client, on_spans.get_finished_spans())

    on_resource = InMemorySpanExporter()  # one org per process: tenant.id on the resource
    provider = TracerProvider(resource=Resource.create({"tenant.id": "Ridge Savings"}))
    provider.add_span_processor(SimpleSpanProcessor(on_resource))
    event = read_events()[0]
    with provider.get_tracer("test").start_as_current_span(event["action"], attributes=span_attributes(event)):
        pass
    provider.shutdown()
    post_pb(client, on_resource.get_finished_spans())

    unnamed = InMemorySpanExporter()  # no tenant at all
    replay(DEFAULT_LOG_DIR, unnamed)
    post_pb(client, unnamed.get_finished_spans())
    live.drain()

    counts = {t["tenant"]: t["events"] for t in client.get("/tenants").json()}
    assert counts == {ORGS[0]: counts[ORGS[0]], ORGS[1]: counts[ORGS[1]], "Ridge Savings": 1, "default": 33}
    assert counts[ORGS[0]] + counts[ORGS[1]] == 33


def test_every_org_runs_every_agent_and_the_bad_sessions_are_spread():
    sessions: dict[str, list[dict]] = {}
    for e in read_events():
        sessions.setdefault(e["session_id"], []).append(e)
    orgs = assign_tenants(sessions, ORGS)
    for org in ORGS:
        agents = {sessions[s][0]["agent_id"] for s, o in orgs.items() if o == org}
        assert agents == {"payments-01", "kyc-01", "disputes-01", "support-01"}
    pressure = {s for s, evs in sessions.items() if evs[0]["pressure_mode"]}
    assert {orgs[s] for s in pressure} == set(ORGS)
    assert assign_tenants(sessions, None) == {s: None for s in sessions}


def test_an_older_database_gets_the_default_tenant(tmp_path):
    path = tmp_path / "old.sqlite"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE events (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, agent_type TEXT NOT NULL, "
        "session_id TEXT NOT NULL, step INTEGER NOT NULL, timestamp TEXT NOT NULL, action TEXT NOT NULL, "
        "params TEXT NOT NULL, auth_level TEXT NOT NULL, policy_check TEXT NOT NULL, outcome TEXT NOT NULL, "
        "reasoning_summary TEXT NOT NULL, pressure_mode INTEGER NOT NULL, source TEXT NOT NULL, "
        "otel_trace_id TEXT, otel_span_id TEXT, received_at TEXT NOT NULL)"
    )
    db.execute(
        "INSERT INTO events VALUES ('payments-01:s_001:1', 'payments-01', 'payments', 's_001', 1, "
        "'2026-11-08T14:00:05.000000Z', 'add_beneficiary', '{}', 'customer_verified', 'passed', 'success', "
        "'x', 0, 'api', NULL, NULL, 'now')"
    )
    db.commit()
    db.close()
    store = Store(path)
    assert store.events()[0]["tenant"] == "default"
    assert store.sessions() == [("default", "payments-01", "s_001")]
