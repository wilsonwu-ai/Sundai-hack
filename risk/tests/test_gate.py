"""Auto-pause: the gate holds risky actions for a person, who approves or rejects them."""

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from risk.api.app import create_app
from risk.ingest.load_logs import DEFAULT_LOG_DIR, read_events
from risk.ingest.replay_otel import replay_live
from risk.scoring.surprise_severity import SurpriseSeverityDetector
from risk.tests.test_detector import FakeRanker

ORG = "Harbor Credit Union"
S_002 = [e for e in read_events() if e["session_id"] == "s_002"]  # step 2 pays a new payee, 2FA skipped


def live_client(tmp_path):
    app = create_app(tmp_path / "m.sqlite", detector=SurpriseSeverityDetector(FakeRanker()))
    return TestClient(app), app.state.live


def test_a_safe_action_is_allowed_and_a_risky_one_is_held(tmp_path):
    client, _ = live_client(tmp_path)
    ok = client.post("/gate", params={"tenant": ORG}, json=S_002[0])
    assert ok.status_code == 200 and ok.json()["allowed"] is True and "hold_id" not in ok.json()

    client.post("/events", params={"tenant": ORG}, json=S_002[0])  # step 1 ran and was recorded
    held = client.post("/gate", params={"tenant": ORG}, json=S_002[1])
    body = held.json()
    assert held.status_code == 202 and body["allowed"] is False and body["status"] == "pending"
    assert body["decision"] == "pause" and "payment_controls" in body["categories"]
    assert body["poll"] == f"/holds/{body['hold_id']}"

    again = client.post("/gate", params={"tenant": ORG}, json=S_002[1]).json()
    assert again["hold_id"] == body["hold_id"]  # asking twice does not queue twice

    queue = client.get("/holds", params={"status": "pending"}).json()
    assert [(h["source"], h["step"], h["tenant"]) for h in queue] == [("gate", 2, ORG)]
    assert "pressure_mode" not in queue[0]["event"] and queue[0]["event"]["action"] == "send_ach"


def test_a_person_decides_once_and_the_agent_sees_it(tmp_path):
    client, _ = live_client(tmp_path)
    client.post("/events", params={"tenant": ORG}, json=S_002[0])
    hold_id = client.post("/gate", params={"tenant": ORG}, json=S_002[1]).json()["hold_id"]
    assert client.get(f"/holds/{hold_id}").json()["status"] == "pending"

    decided = client.post(f"/holds/{hold_id}/decision", json={"status": "approved", "reviewer": "Dana", "note": "Called the customer"})
    assert decided.status_code == 200
    h = decided.json()
    assert (h["status"], h["reviewer"], h["note"]) == ("approved", "Dana", "Called the customer") and h["decided_at"]
    assert client.post("/gate", params={"tenant": ORG}, json=S_002[1]).json()["allowed"] is True

    assert client.post(f"/holds/{hold_id}/decision", json={"status": "rejected", "reviewer": "Sam"}).status_code == 409
    assert client.post(f"/holds/{hold_id}/decision", json={"status": "maybe", "reviewer": "Sam"}).status_code == 422
    assert client.post(f"/holds/{hold_id}/decision", json={"status": "approved", "reviewer": ""}).status_code == 422
    assert client.post("/holds/999/decision", json={"status": "approved", "reviewer": "Sam"}).status_code == 404
    assert client.get("/holds/999").status_code == 404


def test_risky_actions_seen_after_the_fact_are_queued_too(tmp_path):
    client, live = live_client(tmp_path)
    client.post("/events", params={"tenant": ORG}, json=S_002)
    live.drain()
    queue = client.get("/holds").json()
    assert sorted((h["step"], h["source"], h["decision"]) for h in queue) == [
        (2, "observed", "pause"), (3, "observed", "pause"), (4, "observed", "pause")
    ]
    assert all(h["status"] == "pending" and h["loss_if_harmful_usd"] > 0 for h in queue)


def test_a_held_action_that_later_runs_keeps_its_one_hold(tmp_path):
    client, live = live_client(tmp_path)
    client.post("/events", params={"tenant": ORG}, json=S_002[0])
    hold_id = client.post("/gate", params={"tenant": ORG}, json=S_002[1]).json()["hold_id"]
    client.post(f"/holds/{hold_id}/decision", json={"status": "approved", "reviewer": "Dana"})
    client.post("/events", params={"tenant": ORG}, json=S_002[1])  # approved, so it ran
    live.drain()
    step2 = [h for h in client.get("/holds").json() if h["step"] == 2]
    assert len(step2) == 1 and step2[0]["source"] == "gate" and step2[0]["status"] == "approved"


def held_replay(answer):
    """Replay the sample logs live through a fake gate that holds s_002 step 2."""
    exporter = InMemorySpanExporter()

    def gate(event, org):
        if event["session_id"] == "s_002" and event["step"] == 2:
            return {"allowed": False, "hold_id": 7}
        return {"allowed": True}

    lines = []
    replay_live(DEFAULT_LOG_DIR, exporter, 0, say=lines.append, gate=gate, hold_status=lambda _: answer, wait=1)
    sent = [(s.attributes["session_id"], s.attributes["step"]) for s in exporter.get_finished_spans() if "step" in s.attributes]
    return sent, lines


def test_replay_waits_at_the_gate_then_runs_an_approved_action():
    sent, lines = held_replay("approved")
    assert [st for sid, st in sent if sid == "s_002"] == [1, 2, 3, 4]
    assert len(sent) == 33 and any("approved by a person" in line for line in lines)


def test_replay_stops_a_session_whose_action_is_rejected():
    sent, lines = held_replay("rejected")
    assert [st for sid, st in sent if sid == "s_002"] == [1]  # steps 2-4 never ran
    assert len(sent) == 30 and any("the agent stops" in line for line in lines)


def test_replay_gives_up_on_a_hold_nobody_decides():
    sent, lines = held_replay("pending")
    assert [st for sid, st in sent if sid == "s_002"] == [1]
    assert any("gave up" in line for line in lines)


def test_plain_labels_cover_every_action_and_role(tmp_path):
    from risk.ingest.load_logs import load_action_vocab

    client, _ = live_client(tmp_path)
    labels = client.get("/labels").json()
    vocab = load_action_vocab()
    assert set(labels["assistants"]) == set(vocab)
    assert {a for actions in vocab.values() for a in actions} <= set(labels["done"])
    assert "send_ach" in labels["money_actions"] and labels["usual"]["confirm_2fa"].startswith("confirm")


# -- the agent team's contract: POST /gate/check (bank/tools/gate.py) ----------------------------

def as_bank_sends(event):
    return {k: v for k, v in event.items() if k not in ("pressure_mode", "outcome")}


def test_gate_check_speaks_the_agents_contract(tmp_path):
    client, _ = live_client(tmp_path)
    client.post("/events", json=S_002[0])  # step 1's span arrived
    r = client.post("/gate/check", json={"event": as_bank_sends(S_002[1]), "history": [
        {"step": 1, "action": "add_beneficiary", "policy_check": "passed", "outcome": "success"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "pause" and 0 <= body["risk"] <= 1
    assert body["reason"].startswith("Skipped its required policy check") and "severity" not in body["reason"]
    hold = client.get(f"/holds/{body['hold_id']}").json()
    assert (hold["source"], hold["status"], hold["action"]) == ("gate_stop", "pending", "send_ach")

    safe = client.post("/gate/check", json={"event": as_bank_sends(S_002[0]), "history": []}).json()
    assert safe["decision"] == "run" and "hold_id" not in safe


def test_gate_check_uses_the_history_when_no_spans_arrived(tmp_path):
    client, _ = live_client(tmp_path)  # nothing stored for this session
    history = [{"step": 1, "action": "add_beneficiary", "policy_check": "passed", "outcome": "success"}]
    body = client.post("/gate/check", json={"event": as_bank_sends(S_002[1]), "history": history}).json()
    assert body["decision"] == "pause" and "without 2FA" in body["reason"]


def test_gate_check_refuses_a_malformed_event(tmp_path):
    client, _ = live_client(tmp_path)
    bad = {**as_bank_sends(S_002[1]), "action": "wire_everything"}
    assert client.post("/gate/check", json={"event": bad, "history": []}).status_code == 422
    assert client.post("/gate/check", json={"history": []}).status_code == 422
