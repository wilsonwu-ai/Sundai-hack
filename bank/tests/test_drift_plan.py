"""--drift-plan: explicit drifters, each with its own first rogue session and drift level."""

import json

import pytest

from bank.sim.engine import SimConfig, choose_drifters
from bank.sim.run_sim import main as run_sim_main, parse_drift_plan


def test_drift_plan_cli_sets_exact_starts_levels_and_labels(tmp_path):
    rc = run_sim_main(["--seed", "4", "--sessions-per-agent", "10", "--drift-plan", "sales:5:0.33,ops:2:0.81",
                       "--no-gate", "--no-otel", "--out", str(tmp_path), "--run-id", "plan"])
    assert rc == 0
    labels = json.loads((tmp_path / "plan" / "labels.json").read_text())
    assert labels["drifters"] == [
        {"agent_type": "sales", "agent_id": "sales-01", "drift_start_session": 4, "drift_level": 0.33},
        {"agent_type": "ops", "agent_id": "ops-01", "drift_start_session": 1, "drift_level": 0.81}]
    sl = labels["session_labels"]
    rogue = sorted(s for s, v in sl.items() if v)
    assert [s for s in rogue if "_sales_" in s][0].endswith("_sales_005")
    assert [s for s in rogue if "_ops_" in s][0].endswith("_ops_002")
    assert len(rogue) == 6 + 9
    events = [json.loads(line) for line in (tmp_path / "plan" / "events.jsonl").read_text().splitlines()]
    assert all(e["pressure_mode"] == sl[e["session_id"]] for e in events)


def test_drift_plan_parsing_and_validation():
    assert parse_drift_plan("ops:12:0.48, sales:5:0.33") == {"ops": (11, 0.48), "sales": (4, 0.33)}
    for bad in ("ops:12", "ops:0:0.5", "ops:x:0.5", "ops:3:0.5,ops:4:0.5"):
        with pytest.raises(ValueError):
            parse_drift_plan(bad)
    for plan in ({"nope": (3, 0.5)}, {"ops": (40, 0.5)}, {"ops": (3, 1.5)}):
        with pytest.raises(ValueError):
            choose_drifters(SimConfig(sessions_per_agent=40, drift_plan=plan))
    with pytest.raises(ValueError):
        choose_drifters(SimConfig(drift_plan={"ops": (3, 0.5)}, force_drifters=["sales"]))


def test_no_drift_plan_keeps_run_ids_unchanged():
    assert SimConfig(seed=42).resolved_run_id() == SimConfig(seed=42, drift_plan=None).resolved_run_id()
    assert SimConfig(seed=42).resolved_run_id() != SimConfig(seed=42, drift_plan={"ops": (3, 0.5)}).resolved_run_id()
