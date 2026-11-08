"""One call: traffic, agent 10's actions on it, and both label sets."""

from .agent import run_netsec
from .traffic import generate_traffic


def generate(n_sessions: int, seed: int, mix: dict | None = None, n_good: int = 3, n_bad: int = 3) -> dict:
    traffic, traffic_labels = generate_traffic(n_sessions, seed, mix)
    netsec, netsec_labels = run_netsec(traffic, seed, n_good, n_bad, traffic_labels=traffic_labels)
    return {"traffic": traffic, "traffic_labels": traffic_labels, "netsec": netsec, "netsec_labels": netsec_labels}
