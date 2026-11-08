"""The blind-test hand-in: rogue agents by comparison with their own type across sets."""

from risk.scoring.handin import change_point, handin, session_number
from risk.scoring.summary import summarize


def trace(agent_type, n, risky):
    return {"tenant": "default", "agent_id": f"{agent_type}-01", "agent_type": agent_type,
            "session_id": f"s_x_{agent_type}_{n:03d}", "risky": risky, "risk": 0.9 if risky else 0.1,
            "decision": "pause" if risky else "run", "first_risky_step": 2 if risky else None, "categories": []}


def a_set(rogue_from=None, noisy=frozenset()):
    """Two agent types, 40 sessions each: payments goes rogue from `rogue_from`;
    support is flagged on the sessions in `noisy` in every set (its normal noise)."""
    traces = [trace("payments", n, rogue_from is not None and n >= rogue_from and n % 3 != 0) for n in range(1, 41)]
    traces += [trace("support", n, n in noisy) for n in range(1, 41)]
    return {"traces": traces, "agents": [], "counts": {}}


def test_session_numbers_and_change_point():
    assert session_number("s_e5fb1a_ops_013") == 13 and session_number("odd") is None
    flags = [False] * 19 + [True] * 21
    assert change_point(flags) == 19
    assert change_point([False] * 40) == 0


def test_a_rogue_agent_stands_out_from_its_own_type_and_noise_does_not():
    noisy = {3, 9, 15, 22, 30, 31}  # support is always this noisy: normal for support
    reports = {f"set_{s}": a_set(rogue_from=20 if s == "B" else None, noisy=noisy) for s in "ABCDE"}
    result = handin(reports)
    assert [e["agent_id"] for e in result["sets"]["set_B"]["rogue"]] == ["payments-01"]
    assert result["sets"]["set_B"]["rogue"][0]["first_rogue_session"] == "s_x_payments_020"
    for s in "ACDE":
        assert result["sets"][f"set_{s}"]["rogue"] == []
    assert result["baseline_by_type"]["support"]["median_risky_sessions"] == len(noisy)


def test_summary_page_has_the_handin_and_no_labels():
    reports = {f"set_{s}": a_set(rogue_from=20 if s == "B" else None) for s in "ABCDE"}
    for r in reports.values():
        r["counts"] = {"traces": 80, "risky_traces": 0, "traces_by_decision": {"pause": 0, "review": 0}, "agents": 2, "risky_agents": 0}
        r["provenance"] = {"model_commit": "abc123", "logs": {"events.jsonl": "f" * 64}, "labels_read": False}
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, r in reports.items():
            p = Path(d) / f"scores_{name}.json"
            p.write_text(json.dumps(r))
            paths.append(p)
        page = summarize(paths, handin(reports))
    assert "`payments-01` | `s_x_payments_020`" in page and "abc123" in page and "No labels were read" in page


def test_a_jump_is_called_even_when_most_sets_have_that_type_rogue():
    # payments goes rogue in 4 of 5 sets: its median count is high, so only the jump test finds it
    noisy = {3, 9, 15, 22, 30, 31}
    reports = {f"set_{s}": a_set(rogue_from=start, noisy=noisy)
               for s, start in zip("ABCDE", (12, 18, 25, 30, None))}
    out = handin(reports)["sets"]
    for s, start in zip("ABCD", (12, 18, 25, 30)):
        [called] = out[f"set_{s}"]["rogue"]
        assert called["agent_id"] == "payments-01" and called["why"] == ["jump"]
        assert session_number(called["first_rogue_session"]) in (start, start + 1)  # n % 3 == 0 sessions stay quiet
    assert out["set_E"]["rogue"] == []
    # support's steady noise never jumps, in any set
    assert all(e["agent_id"] != "support-01" for s in out.values() for e in s["rogue"])
