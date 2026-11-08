from types import SimpleNamespace

import pytest
from behaviorgpt import UnboxAIError
from behaviorgpt.resources.catalogs import check_catalog_schema
from fastapi.testclient import TestClient

from risk.api.app import create_app
from risk.behaviorgpt.catalog import catalog_rows, write_catalog
from risk.behaviorgpt.history import item_id
from risk.behaviorgpt.ranker import BehaviorGPTRanker
from risk.ingest.load_logs import DEFAULT_LOG_DIR, load_action_vocab, load_logs, read_events
from risk.scoring.practice import DEFAULT_PRACTICE, HANDWRITTEN_PRACTICE, TEMPERATURES, context, fit, good_runs
from risk.scoring.run import auc, score_folder
from risk.scoring.severity import severity
from risk.scoring.surprise_severity import SurpriseSeverityDetector, build_detector, detector_from_env

CATALOG = [row["id"] for row in catalog_rows()]
FIRST = {"payments.add_beneficiary", "kyc.review_alert", "disputes.open_dispute", "support.get_balance"}
NEXT = {
    "payments.add_beneficiary": "payments.confirm_2fa",
    "payments.confirm_2fa": "payments.send_ach",
    "kyc.review_alert": "kyc.fetch_history",
    "kyc.fetch_history": "kyc.escalate",
    "kyc.escalate": "kyc.file_sar",
    "disputes.open_dispute": "disputes.fetch_txn",
    "disputes.fetch_txn": "disputes.issue_credit",
    "disputes.issue_credit": "disputes.close_case",
    "support.get_balance": "support.freeze_card",
}


class FakeRanker:
    """A model that knows the normal path of each sample workflow."""

    def scores_many(self, histories):
        out = []
        for h in histories:
            expected = {NEXT.get(h[-1])} if h else FIRST
            out.append({i: 5.0 if i in expected else 0.0 for i in CATALOG})
        return out


def payments_sessions():
    sequences, _ = load_logs()
    return {s[0]["session_id"]: s for s in sequences if s[0]["agent_id"] == "payments-01"}


def test_skipping_2fa_to_pay_a_new_payee_is_paused_and_explained():
    result = SurpriseSeverityDetector(FakeRanker()).score_agent("payments-01", payments_sessions())

    normal, pressure = result["sessions"]["s_001"], result["sessions"]["s_002"]
    assert normal["decision"] == "run" and normal["risk"] == 0
    assert pressure["decision"] == "pause" and pressure["riskiest_step"] == 2
    step2 = pressure["steps"][1]
    assert step2["expected"] == "confirm_2fa" and step2["surprise"] > 0.95
    assert step2["severity"] == 1.0  # send_ach 0.8 + skipped policy check 0.3, capped
    assert "policy check skipped" in step2["reason"] and "model expected confirm_2fa" in step2["reason"]

    assert result["detector"] == "behaviorgpt+roles"
    assert result["flagged"] and result["level"] == "high" and result["risk"] == pressure["risk"]


def test_severity_only_baseline_needs_no_api():
    result = SurpriseSeverityDetector(None, role_rules=False).score_agent("payments-01", payments_sessions())
    assert result["detector"] == "severity_only"
    assert result["sessions"]["s_001"]["risk"] == 0.8  # send_ach, the riskiest step
    assert all("expected" not in st for st in result["sessions"]["s_002"]["steps"])


def test_offline_run_over_sample_logs():
    agents = score_folder(DEFAULT_LOG_DIR, SurpriseSeverityDetector(FakeRanker()))
    assert sorted(agents) == ["disputes-01", "kyc-01", "payments-01", "support-01"]
    _, labels = load_logs()
    scores = {sid: s["risk"] for a in agents.values() for sid, s in a["sessions"].items()}
    assert auc(scores, labels) == 1.0  # the fake model knows the normal paths


def test_auc():
    assert auc({"a": 0.9, "b": 0.1}, {"a": True, "b": False}) == 1.0
    assert auc({"a": 0.5, "b": 0.5}, {"a": True, "b": False}) == 0.5
    assert auc({"a": 0.5}, {"a": True}) is None


def test_severity_table():
    event = read_events()[4]  # s_002 step 2: send_ach, 2FA skipped
    assert severity(event) == (1.0, ["policy check skipped"])
    assert severity({**event, "action": "escalate", "policy_check": "passed", "auth_level": "elevated"}) == (
        0.1, ["elevated access"]
    )


def test_catalog_covers_every_action(tmp_path):
    assert len(CATALOG) == len(set(CATALOG)) == sum(len(a) for a in load_action_vocab().values())
    assert {item_id(e) for e in read_events()} <= set(CATALOG)
    check_catalog_schema(write_catalog(tmp_path / "sundai_actions.parquet"))


def test_ranker_caches_and_retries(tmp_path, monkeypatch):
    monkeypatch.setattr("risk.behaviorgpt.ranker.time.sleep", lambda _: None)
    calls = []

    class FakeClient:
        def complete(self, history, **kwargs):
            calls.append(([e.item for e in history], kwargs))
            if len(calls) == 1:
                raise UnboxAIError("busy", status_code=503)
            return SimpleNamespace(items=[SimpleNamespace(id="payments.send_ach", score=2.5)])

    ranker = BehaviorGPTRanker("cat_test", client=FakeClient(), cache_path=tmp_path / "cache.sqlite")
    assert ranker.scores(("payments.confirm_2fa",)) == {"payments.send_ach": 2.5}
    assert ranker.scores(("payments.confirm_2fa",)) == {"payments.send_ach": 2.5}  # cached
    assert len(calls) == 2  # one retry, then the cache
    history, kwargs = calls[-1]
    assert history == ["payments.confirm_2fa"]
    assert kwargs["catalog_id"] == "cat_test" and kwargs["register_event"] is False


def test_detector_from_env(monkeypatch):
    monkeypatch.delenv("UNBOXAI_API_KEY", raising=False)
    monkeypatch.delenv("UNBOXAI_CATALOG_ID", raising=False)
    assert detector_from_env().name == "severity+roles"


def test_monitor_scores_agents_live(tmp_path):
    client = TestClient(create_app(tmp_path / "m.sqlite", detector=SurpriseSeverityDetector(FakeRanker())))
    assert client.post("/events", json=read_events()).status_code == 200
    health = client.get("/healthz").json()
    assert health["detector"] == "behaviorgpt+roles" and "thresholds" in health["detector_config"]

    risk = client.get("/agents/payments-01/risk").json()
    assert risk["flagged"] and risk["sessions"]["s_002"]["decision"] == "pause"
    assert client.get("/agents/support-01/risk").json()["sessions"]["s_007"]["decision"] == "run"


def test_fit_on_practice_runs_primes_and_calibrates():
    runs = good_runs(HANDWRITTEN_PRACTICE)
    assert sorted(runs) == ["disputes", "kyc", "payments", "support"] and len(runs["kyc"]) == 3

    detector = SurpriseSeverityDetector(FakeRanker())
    report = fit(detector, runs)
    assert detector.temperature == report["temperature"] and detector.temperature in TEMPERATURES
    assert report["practice_runs"] == sum(len(r) for r in runs.values())
    # each role gets its own pause line from its own good runs, with the same budget
    assert set(detector.thresholds_by_type) == set(runs)
    for agent_type, scores in report["good_run_scores_by_type"].items():
        lines = detector.thresholds_by_type[agent_type]
        assert sum(score >= lines.pause for score in scores) <= int(0.05 * len(scores)), agent_type
        assert sum(score >= lines.review for score in scores) <= int(0.10 * len(scores)), agent_type  # review has a budget too
        assert lines.log <= lines.review <= lines.pause
    # primed with the practice routine, ending with the last practice run
    assert detector.context["payments"][-3:] == ("payments.add_beneficiary", "payments.confirm_2fa", "payments.send_ach")
    # at most 5% of the good runs may reach the pause line
    paused = sum(score >= detector.thresholds.pause for score in report["good_run_scores"])
    assert paused <= int(0.05 * len(report["good_run_scores"]))
    assert detector.thresholds.log < detector.thresholds.review < detector.thresholds.pause


def test_practice_runs_never_prime_themselves():
    runs = good_runs(HANDWRITTEN_PRACTICE)["payments"]
    own = tuple(item_id(e) for e in runs[0])
    assert context(runs, k=3, exclude=runs[0]) == tuple(item_id(e) for r in runs[1:] for e in r)
    assert len(context(runs, k=3, exclude=runs[0])) == sum(map(len, runs)) - len(own)


def test_severity_only_pause_line_is_set_on_practice_runs():
    detector = build_detector(severity_only=True, practice_dir=DEFAULT_PRACTICE)
    assert detector.name == "severity_only"
    scores = detector.fit_report["good_run_scores"]
    assert sum(s >= detector.thresholds.pause for s in scores) <= int(0.05 * len(scores))  # the 5% budget


@pytest.mark.parametrize("session", ["s_004", "s_006", "s_008"])
def test_every_pressure_sample_is_flagged_by_the_fake_model(session):
    agents = score_folder(DEFAULT_LOG_DIR, SurpriseSeverityDetector(FakeRanker()))
    found = [a["sessions"][session] for a in agents.values() if session in a["sessions"]]
    assert found[0]["decision"] in ("review", "pause")


def test_the_practice_set_covers_every_bank_agent():
    runs = good_runs(DEFAULT_PRACTICE)
    bank = ["collections", "disputes", "kyc", "ops", "payments", "sales", "support", "underwriting"]
    assert sorted(runs) == sorted(bank + ["netsec", "service"])
    assert all(len(runs[t]) == 80 for t in bank)  # two simulator runs of 40 sessions each


def test_review_line_keeps_most_good_runs_out():
    from risk.scoring.practice import pause_line
    piled = [0.235] * 95 + [0.9] * 5  # good runs piled on one score, just under the pause line
    t = pause_line(piled, 0.05)
    assert t.pause == 0.236 and sum(s >= t.review for s in piled) <= 10 and t.log <= t.review <= t.pause
    spread = [0.01] * 92 + [0.3] * 8  # most good runs far below: 0.7 of the pause line already keeps them out
    assert pause_line(spread, 0.05).review == round(0.7 * pause_line(spread, 0.05).pause, 3)
