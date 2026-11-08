"""Dollars: what a trace puts at stake if it is unsafe, and the loss to expect.

Per step, from the role table (risk/scoring/roles.py):

- exposure: the amount the action moves (from its params), or for an action that
  moves no money, the role's assumed cost of doing it wrongly;
- loss if harmful: exposure x (1 - the share usually recovered).

Per trace (session), as of each step, so the gate's view at step k uses steps 1..k:

- at stake if unsafe: the sum of loss-if-harmful so far;
- P(unsafe): read off the trace's risk so far (its highest step risk), along a
  line through anchors set at the detector's own log / review / pause lines;
- expected loss = P(unsafe) x at stake;
- held by a pause: loss-if-harmful from the first paused step on, the money a
  gate that stopped there would have kept in the bank.

Risk scores are not probabilities. Until a labelled dev set exists to calibrate
on (the practice set holds only good runs), P(unsafe) at each line is an
assumption, like the role table: change P_AT below.
"""

from risk.scoring.roles import ROLES, amount, units

# P(unsafe) assumed at risk 0, at each decision line, and at risk 1
P_AT = {"zero": 0.01, "log": 0.05, "review": 0.20, "pause": 0.50, "one": 0.90}


def step_exposure(event: dict) -> tuple[float, float]:
    """(exposure, loss if harmful) in dollars for one action."""
    role = ROLES.get(event["agent_type"])
    action = event["action"]
    if role is None:
        return 0.0, 0.0
    if action in role.money:
        moved = amount(event) or 0.0
        return moved, moved * (1 - role.money[action])
    if action in role.costs:
        cost = role.costs[action] * units(event)
        return cost, cost
    return 0.0, 0.0


def p_unsafe(risk: float, thresholds) -> float:
    """Piecewise-linear through (0, P0), (log, P_log), (review, P_review), (pause, P_pause), (1, P1).
    A line above 1 (a pause line set so high nothing pauses) is left out."""
    points = [(0.0, P_AT["zero"])]
    for name in ("log", "review", "pause"):
        x = getattr(thresholds, name)
        if points[-1][0] < x < 1.0:
            points.append((x, P_AT[name]))
    points.append((1.0, P_AT["one"]))
    risk = min(max(risk, 0.0), 1.0)
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if risk <= x1:
            return y0 + (y1 - y0) * (risk - x0) / (x1 - x0)
    return points[-1][1]


def add_dollars(events: list[dict], steps: list[dict], thresholds) -> dict:
    """Write each step's dollars into `steps` (as of that step) and return the trace's."""
    at_stake, top_risk, held, paused = 0.0, 0.0, 0.0, False
    for event, step in zip(events, steps):
        exposure, loss = step_exposure(event)
        at_stake += loss
        top_risk = max(top_risk, step["risk"])
        p = p_unsafe(top_risk, thresholds)
        paused = paused or step["decision"] == "pause"
        held += loss if paused else 0.0
        step |= {
            "exposure_usd": round(exposure, 2),
            "loss_if_harmful_usd": round(loss, 2),
            "at_stake_usd": round(at_stake, 2),
            "p_unsafe": round(p, 3),
            "expected_loss_usd": round(p * at_stake, 2),
        }
    role = ROLES.get(events[0]["agent_type"]) if events else None
    last = steps[-1] if steps else {"at_stake_usd": 0.0, "p_unsafe": 0.0, "expected_loss_usd": 0.0}
    return {
        "at_stake_usd": last["at_stake_usd"],
        "p_unsafe": last["p_unsafe"],
        "expected_loss_usd": last["expected_loss_usd"],
        "held_by_pause_usd": round(held, 2),
        "worth_a_review": last["expected_loss_usd"] >= (role.review_cost_usd if role else 15.0),
    }


def agent_dollars(sessions: dict[str, dict]) -> dict:
    """An agent's dollars: its traces' sums."""
    return {
        "at_stake_usd": round(sum(s["at_stake_usd"] for s in sessions.values()), 2),
        "expected_loss_usd": round(sum(s["expected_loss_usd"] for s in sessions.values()), 2),
        "held_by_pause_usd": round(sum(s["held_by_pause_usd"] for s in sessions.values()), 2),
        "sessions_worth_a_review": sum(s["worth_a_review"] for s in sessions.values()),
    }
