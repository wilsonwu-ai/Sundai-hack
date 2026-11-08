"""The netsec agent results script: per-attack breakdown and the hero cases."""

from risk.eval.netsec import HERO_CASES, by_attack, page, score_heroes
from risk.scoring.surprise_severity import SurpriseSeverityDetector
from risk.tests.test_detector import FakeRanker


def test_by_attack_counts_pauses_before_harm():
    report = {"traces": [
        {"session_id": "a", "risky": True, "decision": "pause", "first_risky_step": 3},
        {"session_id": "b", "risky": True, "decision": "pause", "first_risky_step": 9},
        {"session_id": "c", "risky": True, "decision": "review", "first_risky_step": 2},
    ]}
    labels = {"a": {"attack": "limit_raise_transfer", "harmful": True, "first_harmful_step": 4},
              "b": {"attack": "limit_raise_transfer", "harmful": True, "first_harmful_step": 4},
              "c": {"attack": "none", "harmful": False, "first_harmful_step": None}}
    rows = by_attack(report, labels)
    assert rows["limit_raise_transfer"] == {"sessions": 2, "harmful": 2, "paused": 2, "paused_before_harm": 1,
                                            "flagged": 2, "normal_flagged": 0, "normal": 0}
    assert rows["none"]["normal_flagged"] == 1


def test_hero_cases_score_and_render():
    heroes = score_heroes(SurpriseSeverityDetector(FakeRanker()))
    assert [h["case"] for h in heroes] == HERO_CASES
    bad = next(h for h in heroes if h["case"] == "bad_attack")
    assert bad["harmful"] and bad["decision"] in ("review", "pause") and "privilege" in bad["categories"]
    assert next(h for h in heroes if h["case"] == "good_normal")["decision"] == "run"
    results = {"provenance": {"model_commit": "abc", "uncommitted_changes": False}, "seeds": [2], "sessions_per_seed": 1,
               "per_seed": {}, "heroes": heroes}
    assert "| bad_attack | bad | limit_raise_transfer | yes (4) |" in page(results)
