import json
import os
from pathlib import Path

import pytest

from bank.sim.engine import SimConfig, Simulation

os.environ.setdefault("OTEL_ENABLED", "0")
os.environ.setdefault("GATE_ENABLED", "0")


def run(out: Path, **kw) -> Simulation:
    """One offline simulation (no OpenTelemetry, no gate unless asked)."""
    kw.setdefault("otel", False)
    kw.setdefault("gate", False)
    sim = Simulation(SimConfig(out=out, **kw))
    sim.prepare()
    sim.run()
    sim.close()
    return sim


def read_events(sim: Simulation) -> list[dict]:
    return [json.loads(line) for line in (sim.dir / "events.jsonl").read_text().splitlines()]


@pytest.fixture(scope="session")
def sim42(tmp_path_factory):
    return run(tmp_path_factory.mktemp("sim42"), seed=42)


@pytest.fixture(scope="session")
def events42(sim42):
    return read_events(sim42)


@pytest.fixture(scope="session")
def sim_all_drift(tmp_path_factory):
    from bank.agents import AGENT_TYPES
    return run(tmp_path_factory.mktemp("alldrift"), seed=5, sessions_per_agent=80, force_drifters=list(AGENT_TYPES))
