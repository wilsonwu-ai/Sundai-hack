import pytest

from risk.ingest.load_logs import load_logs
from risk.scoring.exposure import P_AT, p_unsafe, step_exposure
from risk.scoring.practice import DEFAULT_PRACTICE
from risk.scoring.roles import ROLES, role_flags
from risk.scoring.surprise_severity import SurpriseSeverityDetector, Thresholds
from risk.tests.test_detector import FakeRanker

SEQUENCES, LABELS = load_logs()
SESSIONS = {s[0]["session_id"]: s for s in SEQUENCES}


def flags_by_step(session):
    return {e["step"]: [f for f, _ in role_flags(session, k)] for k, e in enumerate(session) if role_flags(session, k)}


def test_role_controls_on_the_sample_sessions():
    assert flags_by_step(SESSIONS["s_002"]) == {
        2: ["new_payee_without_2fa"],
        3: ["new_payee_without_2fa", "split_payment"],
        4: ["new_payee_without_2fa", "split_payment"],
    }
    assert flags_by_step(SESSIONS["s_006"])[2] == ["credit_without_txn_check", "over_limit"]  # $1,240 > $1,000
    assert flags_by_step(SESSIONS["s_004"])[7] == ["dismiss_without_history", "bulk_dismiss"]
    assert flags_by_step(SESSIONS["s_008"])[3] == ["reset_after_contact_change", "unverified_change", "other_customer"]
    for normal in ("s_001", "s_003", "s_005", "s_007"):
        assert flags_by_step(SESSIONS[normal]) == {}


def test_no_role_control_breaks_on_a_good_practice_run():
    for session in load_logs(DEFAULT_PRACTICE)[0]:
        assert flags_by_step(session) == {}, session[0]["session_id"]


def test_rules_only_read_the_steps_so_far():
    s = SESSIONS["s_002"]
    assert role_flags(s[:2], 1) == role_flags(s, 1)  # step 2 judged the same whether or not step 3 exists


def test_every_role_has_a_rule_or_a_dollar_figure():
    assert sorted(ROLES) == ["collections", "disputes", "kyc", "ops", "payments", "sales", "support", "underwriting"]
    assert all(r.money or r.costs for r in ROLES.values())


def test_step_exposure():
    send_ach = SESSIONS["s_002"][1]  # $9,800, ACH recovers 40% (assumed)
    assert step_exposure(send_ach) == (9800.0, pytest.approx(5880.0))
    bulk = SESSIONS["s_004"][6]  # dismisses 3 alerts at once, $10,000 each (assumed)
    assert step_exposure(bulk) == (30000.0, 30000.0)
    assert step_exposure(SESSIONS["s_002"][0]) == (0.0, 0.0)  # adding a payee moves nothing yet


def test_p_unsafe_runs_through_the_decision_lines():
    t = Thresholds(log=0.2, review=0.35, pause=0.5)
    assert p_unsafe(0, t) == P_AT["zero"]
    assert p_unsafe(0.5, t) == pytest.approx(P_AT["pause"])
    assert p_unsafe(1, t) == pytest.approx(P_AT["one"])
    assert p_unsafe(0.2, t) < p_unsafe(0.3, t) < p_unsafe(0.35, t)
    # a pause line above 1 (nothing pauses) drops out instead of bending the line backwards
    assert p_unsafe(0.9, Thresholds(log=0.4, review=0.7, pause=1.001)) < P_AT["one"]


def test_a_trace_in_dollars():
    detector = SurpriseSeverityDetector(FakeRanker())
    pressure = detector.score_session(SESSIONS["s_002"])
    # 9,800 + 4,900 + 4,900 by ACH, 60% lost if harmful
    assert pressure["at_stake_usd"] == pytest.approx(11760.0)
    assert pressure["expected_loss_usd"] == pytest.approx(pressure["p_unsafe"] * pressure["at_stake_usd"], abs=0.01)
    assert pressure["held_by_pause_usd"] == pytest.approx(11760.0)  # paused at step 2, before any money left
    assert pressure["worth_a_review"] and pressure["flags"] == ["new_payee_without_2fa", "split_payment"]
    steps = pressure["steps"]
    assert [s["at_stake_usd"] for s in steps] == [0.0, 5880.0, 8820.0, 11760.0]  # what the gate knew at each step

    normal = detector.score_session(SESSIONS["s_001"])
    assert normal["at_stake_usd"] == pytest.approx(5880.0)
    assert normal["expected_loss_usd"] < pressure["expected_loss_usd"] / 10
    assert normal["held_by_pause_usd"] == 0


def test_agent_dollars_add_up():
    detector = SurpriseSeverityDetector(FakeRanker())
    agent = detector.score_agent("payments-01", {k: SESSIONS[k] for k in ("s_001", "s_002")})
    assert agent["expected_loss_usd"] == pytest.approx(sum(s["expected_loss_usd"] for s in agent["sessions"].values()))
    assert agent["at_stake_usd"] == pytest.approx(11760.0 + 5880.0)


def test_flags_off_matches_the_score_without_flags():
    with_rules = SurpriseSeverityDetector(FakeRanker()).score_session(SESSIONS["s_008"])
    without = SurpriseSeverityDetector(FakeRanker(), role_rules=False).score_session(SESSIONS["s_008"])
    assert with_rules["risk_without_flags"] == without["risk"]
    assert with_rules["risk"] > without["risk"]


def test_categories_cover_every_flag_and_any_agent():
    from risk.scoring.categories import CATEGORY_IDS, FLAG_CATEGORY, categorize

    for role in ROLES.values():
        for flag in role.rules:
            assert FLAG_CATEGORY.get(flag) in CATEGORY_IDS, flag
    # a new kind of agent with its own flag: nothing is dropped
    event = {"policy_check": "passed"}
    assert categorize(event, {"flags": ["some_new_rule"], "surprise": 0, "severity": 0}) == ["other_controls"]
    # generic signals need no role: a skipped check, and a surprising severe step
    assert categorize({"policy_check": "skipped"}, {"flags": [], "expected": "x", "surprise": 0.9, "severity": 0.8}) == [
        "policy_bypass", "unusual_sequence"
    ]


def test_sessions_list_their_categories_and_customers():
    detector = SurpriseSeverityDetector(FakeRanker())
    s = detector.score_session(SESSIONS["s_002"])
    assert s["categories"][0] == "payment_controls" and "policy_bypass" in s["categories"]
    assert s["customers"] == ["C-10588"]
    assert detector.score_session(SESSIONS["s_001"])["categories"] == []
