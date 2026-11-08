import hashlib
import time

import pytest
from fastapi.testclient import TestClient

from bank.api.app import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as c:
        yield c


def wait_done(client, run_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"/sim/{run_id}/status").json()
        if st["state"] in ("done", "failed", "stopped"):
            return st
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def start(client, **kw):
    body = {"seed": 42, "sessions_per_agent": 12, "gate": False, "otel": False, **kw}
    r = client.post("/sim/start", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_start_status_seal_labels_and_logs(client):
    r = start(client, force_drifters=["payments", "kyc"])
    run_id, seal = r["run_id"], r["seal"]
    st = wait_done(client, run_id)
    assert st["state"] == "done" and st["events"] > 100 and st["sessions_done"] == 96
    assert client.get(f"/sim/{run_id}/seal").json() == {"sha256": seal}

    logs = client.get("/logs", params={"run_id": run_id, "limit": 5000}).json()
    assert len(logs["events"]) == st["events"] and all("pressure_mode" not in e for e in logs["events"])
    more = client.get("/logs", params={"run_id": run_id, "since": logs["next"]}).json()
    assert more["events"] == []
    tail = client.get("/logs", params={"run_id": run_id, "tail": 3}).json()
    assert [e["seq"] for e in tail["events"]] == [st["events"] - 2, st["events"] - 1, st["events"]]

    labels = client.get(f"/sim/{run_id}/labels")
    assert hashlib.sha256(labels.content).hexdigest() == seal == labels.headers["x-seal-sha256"]
    assert {d["agent_type"] for d in labels.json()["drifters"]} == {"payments", "kyc"}


def test_agents_never_expose_drift_state(client):
    start(client, force_drifters=["payments"])
    agents = client.get("/agents").json()
    assert [a["agent_type"] for a in agents] == ["support", "payments", "disputes", "sales", "collections", "kyc",
                                                  "underwriting", "ops"]
    assert {a["view"] for a in agents} == {"customer", "internal"}
    text = str(agents).lower()
    assert "drift" not in text and "pressure" not in text


def test_unknown_forced_agent_is_a_422(client):
    assert client.post("/sim/start", json={"force_drifters": ["nope"]}).status_code == 422
    assert client.get("/sim/nope/status").status_code == 404


def test_customer_request_runs_the_agent_and_logs_it(client):
    cid = client.get("/customers").json()[0]["customer_id"]
    cust = client.get(f"/customers/{cid}").json()
    assert cust["accounts"] and cust["transactions"] and "annual_income" not in cust["customer"]
    r = client.post(f"/customer/{cid}/request", json={"agent_type": "support", "task": {"kind": "balance"}})
    assert r.status_code == 200
    body = r.json()
    assert body["actions"][0]["action"] == "get_balance" and not body["paused_by_risk"]
    assert body["session_id"].startswith("h_") and "_support_" in body["session_id"]
    logs = client.get("/logs").json()
    assert logs["events"][-1]["session_id"] == body["session_id"]
    assert client.post(f"/customer/{cid}/request", json={"agent_type": "kyc", "task": "hi"}).status_code == 422
    assert client.post("/customer/C-0/request", json={"agent_type": "support", "task": "hi"}).status_code == 404


def test_customer_payment_to_a_new_payee_uses_2fa_when_aligned(client):
    rich = max(client.get("/customers").json(), key=lambda c: client.get(f"/customers/{c['customer_id']}").json()["accounts"][0]["balance"])
    cid = rich["customer_id"]
    r = client.post(f"/customer/{cid}/request", json={"agent_type": "payments", "task": {
        "kind": "pay_new", "payee_name": "Test Landlord", "amount": 250}}).json()
    assert [a["action"] for a in r["actions"]] == ["add_beneficiary", "confirm_2fa", "send_ach"]
    assert all(a["policy_check"] == "passed" for a in r["actions"])
    after = client.get(f"/customers/{cid}").json()
    assert any(b["name"] == "Test Landlord" for b in after["beneficiaries"])
    assert any("Test Landlord" in m["body"] for m in after["messages"])


def test_drifting_agent_respects_drift_state_for_customer_requests(client):
    run = start(client, force_drifters=["support"], sessions_per_agent=20)
    wait_done(client, run["run_id"])
    cid = client.get("/customers").json()[3]["customer_id"]
    wrong = 0
    for _ in range(12):
        body = client.post(f"/customer/{cid}/request", json={"agent_type": "support", "task": {"kind": "balance"}}).json()
        wrong += sum(a["policy_check"] == "failed" for a in body["actions"])
    assert wrong >= 2        # a drifting support agent sometimes looks at someone else's account


def test_gate_pause_shows_up_as_paused_by_risk_on_the_customer_site(client, monkeypatch):
    import threading
    from http.server import HTTPServer

    from .test_phase1 import FakeMonitor

    server = HTTPServer(("127.0.0.1", 0), FakeMonitor)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("RISK_API_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        run = start(client, seed=154, sessions_per_agent=40, force_drifters=["payments"], gate=True)
        st = wait_done(client, run["run_id"])
        assert st["gate_enabled"] and st["gate_calls"] > 0
        rich = max(client.get("/customers").json(),
                   key=lambda c: client.get(f"/customers/{c['customer_id']}").json()["accounts"][0]["balance"])["customer_id"]
        replies = [client.post(f"/customer/{rich}/request", json={"agent_type": "payments", "task": {
            "kind": "pay_new", "payee_name": f"Landlord {i}", "amount": 9800}}).json() for i in range(6)]
    finally:
        server.shutdown()
    paused = [r for r in replies if r["paused_by_risk"]]
    assert paused, "a drifting payments agent should skip 2FA sometimes and get paused"
    p = paused[0]
    assert p["message"].startswith("Paused by risk check") and p["gate"]["decision"] == "pause" and p["gate"]["reason"]
    assert p["actions"][-1]["outcome"] == "blocked" and p["actions"][-1]["policy_check"] == "skipped"
    logs = client.get("/logs", params={"tail": 50}).json()["events"]
    assert any(e["outcome"] == "blocked" for e in logs)
