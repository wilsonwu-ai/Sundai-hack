"""Where the risk engine plugs in, the same offline and online.

A detector scores one agent from its sessions: `sessions` maps session_id to
that session's events in step order, shaped as shared/log_schema.json WITHOUT
`pressure_mode` (the label never reaches a detector).

- Offline: `sessions_by_agent(load_logs(folder)[0])`
- Online: `GET /agents/{id}/risk` passes what the monitor has stored

Implement `Detector` and pass it to `create_app(detector=...)` in risk/api/server.py.
"""

from typing import Protocol


class Detector(Protocol):
    def score_agent(self, agent_id: str, sessions: dict[str, list[dict]]) -> dict:
        """Return a JSON-able verdict, e.g.
        {"risk": 0.0-1.0, "flagged": bool, "sessions": {session_id: {...}}}."""
        ...


class NoDetector:
    def score_agent(self, agent_id: str, sessions: dict[str, list[dict]]) -> dict:
        return {
            "agent_id": agent_id,
            "status": "no_detector",
            "risk": None,
            "flagged": None,
            "sessions": {session_id: None for session_id in sessions},
        }


def sessions_by_agent(sequences: list[list[dict]]) -> dict[str, dict[str, list[dict]]]:
    """load_logs() sequences as detectors take them: agent_id -> session_id -> events."""
    agents: dict[str, dict[str, list[dict]]] = {}
    for session in sequences:
        first = session[0]
        agents.setdefault(first["agent_id"], {})[first["session_id"]] = session
    return agents
