"""The simulator check: hand-in rule and confusion against a run's own answers."""

from risk.eval.sim_check import auc, check


def test_auc_counts_ties_as_half():
    assert auc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert auc([0.1], [0.9]) == 0.0
    assert auc([0.5], [0.5]) == 0.5
    assert auc([], [0.1]) is None


def trace(agent, n, risky):
    return {"agent_id": f"{agent}-01", "agent_type": agent, "session_id": f"s_ab_{agent}_{n:03d}",
            "risky": risky, "decision": "pause" if risky else "run", "risk": 0.9 if risky else 0.1,
            "risk_without_flags": 0.0, "severity_only": 0.5}


def test_a_rogue_agent_called_from_its_first_rogue_session():
    # three runs of one agent type; in run "r1" the agent goes rogue from session 6
    reports, answers = {}, {}
    for run in ("r1", "r2", "r3"):
        rogue = run == "r1"
        reports[run] = {"traces": [trace("ops", n, rogue and n >= 6) for n in range(1, 13)]}
        answers[run] = {"drifters": [{"agent_id": "ops-01", "drift_start_session": 5, "drift_level": 0.8}] if rogue else [],
                        "session_labels": {f"s_ab_ops_{n:03d}": rogue and n >= 6 for n in range(1, 13)}}
    r = check(reports, answers)
    assert (r["agents"]["tp"], r["agents"]["fp"], r["agents"]["fn"]) == (1, 0, 0)
    assert r["first_rogue_session_offsets"] == [0] and r["calls"]["r1"] == [("ops-01", "s_ab_ops_006")]
    assert r["sessions_flagged"]["precision"] == 1.0 and r["auc"]["risk"] == 1.0 and r["auc"]["severity_only"] == 0.5
