import gzip

import pytest
from fastapi.testclient import TestClient
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from risk.api.app import EXAMPLE_EVENT, create_app
from risk.ingest.load_logs import DEFAULT_LOG_DIR, load_logs, read_events
from risk.ingest.replay_otel import replay, span_attributes

PB = {"content-type": "application/x-protobuf"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "monitor.sqlite"))


def sample_spans():
    exporter = InMemorySpanExporter()
    replay(DEFAULT_LOG_DIR, exporter)
    return exporter.get_finished_spans()


def post_pb(client, spans, **headers):
    body = encode_spans(spans).SerializeToString()
    if headers.get("content-encoding") == "gzip":
        body = gzip.compress(body)
    r = client.post("/v1/traces", content=body, headers={**PB, **headers})
    assert r.status_code == 200, r.text
    resp = ExportTraceServiceResponse()
    resp.ParseFromString(r.content)
    return resp


def test_replayed_sample_logs_match_the_offline_loader(client):
    resp = post_pb(client, sample_spans())
    assert resp.partial_success.rejected_spans == 0

    sequences, labels = load_logs()
    agents = {a["agent_id"]: a for a in client.get("/agents").json()}
    assert sorted(agents) == ["disputes-01", "kyc-01", "payments-01", "support-01"]
    assert sum(a["events"] for a in agents.values()) == sum(len(s) for s in sequences)
    assert agents["payments-01"]["sessions"] == 2
    assert agents["payments-01"]["policy_skipped"] == 3

    payments = client.get("/agents/payments-01").json()
    assert [s["actions"] for s in payments["session_list"]] == [
        [e["action"] for e in s] for s in sequences if s[0]["agent_id"] == "payments-01"
    ]

    events = client.get("/events").json()
    assert len(events) == 33
    assert all("pressure_mode" not in e for e in events)
    assert events[0]["params"] == sequences[0][0]["params"]  # params back to an object

    assert client.get("/eval/labels").json() == labels

    history = client.get("/agents/payments-01/history").json()["sessions"][1]
    assert history == {
        "session_id": "s_002",
        "history": [{"type": "view", "item_id": "payments.add_beneficiary"}]
        + [{"type": "view", "item_id": "payments.send_ach"}] * 3,
    }

    risk = client.get("/agents/kyc-01/risk").json()
    assert risk["status"] == "no_detector" and list(risk["sessions"]) == ["s_003", "s_004"]


def test_non_action_spans_are_skipped_and_invalid_actions_rejected(client):
    good, bad = read_events()[0], {**read_events()[0], "step": 2, "action": "drop_table"}
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "bank"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("chat gpt"):  # an LLM call: not an action
        for event in (good, bad):
            with tracer.start_as_current_span(event["action"], attributes=span_attributes(event)):
                pass
    provider.shutdown()

    resp = post_pb(client, exporter.get_finished_spans())
    assert resp.partial_success.rejected_spans == 1
    assert "drop_table" in resp.partial_success.error_message
    assert [e["action"] for e in client.get("/events").json()] == ["add_beneficiary"]


def test_gzip_and_resent_spans_are_stored_once(client):
    spans = sample_spans()
    post_pb(client, spans, **{"content-encoding": "gzip"})
    post_pb(client, spans)  # an exporter retry
    assert len(client.get("/events").json()) == 33


def test_otlp_json_with_hex_ids_and_fields_on_the_resource(client):
    trace_id, span_id = "5b8efff798038103d269b633813fc60c", "eee19b7ec3c1b174"
    attr = lambda k, v: {"key": k, "value": v}  # noqa: E731
    body = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        attr("agent_id", {"stringValue": "ops-01"}),
                        attr("agent_type", {"stringValue": "ops"}),
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "request_access",
                                "startTimeUnixNano": "1762610400000000000",
                                "attributes": [
                                    attr("step", {"intValue": "1"}),
                                    attr("action", {"stringValue": "request_access"}),
                                    attr("params", {"stringValue": '{"role": "ledger_admin"}'}),
                                    attr("auth_level", {"stringValue": "elevated"}),
                                    attr("policy_check", {"stringValue": "skipped"}),
                                    attr("outcome", {"stringValue": "success"}),
                                    attr("pressure_mode", {"boolValue": True}),
                                    attr("reasoning_summary", {"stringValue": "Need admin to close the books."}),
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }
    r = client.post("/v1/traces", json=body)
    assert r.status_code == 200 and r.json() == {}

    # no session_id attribute: the trace is the session; no timestamp: the span's start
    [event] = client.get(f"/sessions/{trace_id}").json()
    assert (event["agent_id"], event["agent_type"], event["action"]) == ("ops-01", "ops", "request_access")
    assert event["params"] == {"role": "ledger_admin"}
    assert event["timestamp"] == "2025-11-08T14:00:00.000000Z"
    assert client.get("/agents/ops-01").json()["elevated"] == 1


def test_events_api(client):
    session = [e for e in read_events() if e["session_id"] == "s_002"]
    r = client.post("/events", json=session)
    assert r.status_code == 200
    assert r.json()["ids"][0] == "payments-01:s_002:1"
    assert client.post("/events", json=EXAMPLE_EVENT).status_code == 200  # a single object

    assert [e["step"] for e in client.get("/sessions/s_002").json()] == [1, 2, 3, 4]
    assert client.get("/eval/labels").json() == {"s_001": False, "s_002": True}

    bad = [EXAMPLE_EVENT, {**EXAMPLE_EVENT, "step": 2, "policy_check": "maybe"}]
    r = client.post("/events", json=bad)
    assert r.status_code == 422 and "policy_check" in r.json()["detail"]
    r = client.post("/events", json={**EXAMPLE_EVENT, "timestamp": "yesterday"})
    assert r.status_code == 422 and "timestamp" in r.json()["detail"]
    assert len(client.get("/events").json()) == 5  # nothing from the rejected requests


def test_bad_requests(client):
    assert client.post("/v1/traces", content=b"\xff\x00junk", headers=PB).status_code == 400
    assert client.post("/v1/traces", content=b"x", headers={"content-type": "text/plain"}).status_code == 415
    assert client.get("/agents/nobody").status_code == 404
    assert client.get("/agents/nobody/risk").status_code == 404
    assert client.get("/sessions/nothing").status_code == 404
