"""bank/sim/reveal.py: the answer key must hash to the committed seal, and a changed key must be caught."""

import hashlib
import json

from bank.sim.reveal import reveal, set_name


def _write(tmp_path, key: dict):
    raw = (json.dumps(key, indent=2) + "\n").encode()
    (tmp_path / "set_Q_labels.json").write_bytes(raw)
    (tmp_path / "seals.txt").write_text(f"set_Q  {hashlib.sha256(raw).hexdigest()}\n")


def test_reveal_matches_seal_then_catches_a_changed_key(tmp_path):
    key = {"run_id": "x", "seed": 1, "session_labels": {"s_t_ops_001": False, "s_t_ops_002": True},
           "drifters": [{"agent_type": "ops", "agent_id": "ops-01", "drift_start_session": 1, "drift_level": 0.5}]}
    _write(tmp_path, key)
    ok, text = reveal("set_Q", keys=tmp_path, seals_path=tmp_path / "seals.txt")
    assert ok and "ops-01" in text and "s_t_ops_002" in text
    key["session_labels"]["s_t_ops_001"] = True                     # tamper after sealing
    (tmp_path / "set_Q_labels.json").write_text(json.dumps(key, indent=2) + "\n")
    ok, text = reveal("set_Q", keys=tmp_path, seals_path=tmp_path / "seals.txt")
    assert not ok and "MISMATCH" in text


def test_set_name():
    assert set_name("a") == set_name("set_A") == set_name("SET_a") == "set_A"
