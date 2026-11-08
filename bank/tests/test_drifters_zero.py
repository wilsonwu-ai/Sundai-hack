"""--drifters 0: a control run where every agent stays aligned, with the answer key still sealed."""

import hashlib
import json

from bank.agents import AGENT_TYPES
from bank.sim.run_sim import main as run_sim_main


def test_drifters_zero_cli_runs_all_aligned_and_still_seals(tmp_path):
    rc = run_sim_main(["--seed", "3", "--sessions-per-agent", "6", "--drifters", "0",
                       "--no-gate", "--no-otel", "--out", str(tmp_path), "--run-id", "control"])
    assert rc == 0
    run = tmp_path / "control"
    raw = (run / "labels.json").read_bytes()
    labels = json.loads(raw)
    assert labels["drifters"] == []
    assert len(labels["session_labels"]) == 6 * len(AGENT_TYPES)
    assert not any(labels["session_labels"].values())
    assert (run / "labels.sha256").read_text().strip() == hashlib.sha256(raw).hexdigest()
    events = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
    assert events and not any(e["pressure_mode"] for e in events)
    assert {e["agent_type"] for e in events} == set(AGENT_TYPES)
