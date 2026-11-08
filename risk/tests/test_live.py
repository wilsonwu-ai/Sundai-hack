from fastapi.testclient import TestClient

from risk.api.app import create_app
from risk.ingest.load_logs import read_events
from risk.scoring.surprise_severity import SurpriseSeverityDetector
from risk.tests.test_detector import FakeRanker


def live_client(tmp_path):
    app = create_app(tmp_path / "m.sqlite", detector=SurpriseSeverityDetector(FakeRanker()))
    return TestClient(app), app.state.live


def test_actions_are_scored_as_they_arrive(tmp_path):
    client, live = live_client(tmp_path)
    client.post("/events", json=read_events())
    live.drain()

    feed = client.get("/live/steps").json()
    assert len(feed["steps"]) == 33 and feed["seq"] == max(s["seq"] for s in feed["steps"])
    assert all("pressure_mode" not in s for s in feed["steps"])

    alerts = client.get("/alerts").json()
    assert ("s_002", 2, "pause") in {(a["session_id"], a["step"], a["decision"]) for a in alerts}
    assert all(a["decision"] in ("review", "pause") for a in alerts)

    curve = client.get("/sessions/s_002/scores").json()
    assert [s["step"] for s in curve] == [1, 2, 3, 4] and curve[1]["expected"] == "confirm_2fa"
    assert client.get("/sessions/nothing/scores").status_code == 404


def test_feed_returns_only_what_changed(tmp_path):
    client, live = live_client(tmp_path)
    session = [e for e in read_events() if e["session_id"] == "s_002"]

    client.post("/events", json=session[:1])
    live.drain()
    first = client.get("/live/steps").json()
    assert [s["step"] for s in first["steps"]] == [1]

    client.post("/events", json=session[1:2])  # the next action arrives
    live.drain()
    new = client.get("/live/steps", params={"since": first["seq"]}).json()
    assert [(s["step"], s["decision"]) for s in new["steps"]] == [(2, "pause")]  # step 1 not rewritten

    client.post("/events", json=session[:2])  # a retry changes nothing
    live.drain()
    assert client.get("/live/steps", params={"since": new["seq"]}).json() == {"seq": new["seq"], "steps": []}


def test_scores_catch_up_after_a_restart(tmp_path):
    client, live = live_client(tmp_path)
    client.post("/events", json=read_events())
    live.drain()

    app = create_app(tmp_path / "m.sqlite", detector=SurpriseSeverityDetector(None, role_rules=False))  # a new detector
    app.state.live.drain()
    steps = TestClient(app).get("/live/steps").json()["steps"]
    assert len(steps) == 33 and {s["detector"] for s in steps} == {"severity_only"}


def test_dashboard_is_served(tmp_path):
    client, _ = live_client(tmp_path)
    page = client.get("/dashboard/")
    assert page.status_code == 200 and "Assistant safety" in page.text
    assert client.get("/dashboard/app.js").status_code == 200
    assert client.get("/dashboard/styles.css").status_code == 200
    assert client.get("/dashboard/fonts/dm-sans-latin.woff2").status_code == 200
    assert client.get("/", follow_redirects=False).headers["location"] == "/dashboard/"
    assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/dashboard/"


def test_steps_carry_categories_and_customers(tmp_path):
    client, live = live_client(tmp_path)
    client.post("/events", json=read_events())
    live.drain()
    step = next(s for s in client.get("/sessions/s_008/scores").json() if s["step"] == 3)
    assert step["customer_id"] == "C-61873"
    assert step["categories"] == ["account_takeover", "data_access", "policy_bypass"]  # the test model has no view after update_contact

    categories = client.get("/categories").json()
    assert [c["id"] for c in categories][:2] == ["payment_controls", "account_takeover"]
    assert all(c["name"] and c["description"] for c in categories)
    flags = client.get("/flags").json()
    assert flags["over_limit"] == {"category": "limits", "fields": flags["over_limit"]["fields"]}
    assert "amount_usd" in flags["over_limit"]["fields"]


def test_without_a_step_detector_there_is_no_live_scoring(tmp_path):
    app = create_app(tmp_path / "m.sqlite")  # NoDetector
    client = TestClient(app)
    client.post("/events", json=read_events())
    assert app.state.live is None
    assert client.get("/live/steps").json() == {"seq": 0, "steps": []}
