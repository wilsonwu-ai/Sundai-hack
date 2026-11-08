"""Risk = surprise x severity, with BehaviorGPT as the next-move model (PRD section 7).

Step k is scored from steps 1..k-1 only, as a live gate would run it:

1. BehaviorGPT ranks the action catalog after a history: normal runs of the
   same agent type (the priming `context`, from the practice set), then the
   session's actions so far. The scores of the agent's own actions (its
   vocabulary in shared/actions.md) become probabilities with a softmax at
   `temperature`, mixed with a little uniform mass (`epsilon`) so no action is
   impossible.
2. surprise = 1 - p(actual) / p(most expected): 0 when the agent did what the
   model expected most, near 1 when it did something the model found unlikely.
3. risk = surprise x severity (risk/scoring/severity.py), plus a bonus for
   each of the role's controls the step breaks (risk/scoring/roles.py:
   FLAG_WEIGHT x severity per flag, capped at 1), and a decision: run, log,
   review or pause, with a plain reason.
4. Dollars (risk/scoring/exposure.py): what the trace puts at stake if it is
   unsafe, P(unsafe) from its risk so far, and the expected loss.

Run score = the highest step risk; agent score = the highest run score, with
the mean alongside (PRD section 8). Without a ranker, surprise is 1 for every
step; without role rules too, that is the PRD's severity-only baseline.

`risk.scoring.practice.fit` sets the context, temperature and thresholds from
a practice set of good runs; the defaults here are only placeholders.
"""

import math
import os
from dataclasses import asdict, dataclass

from risk.behaviorgpt.history import item_id
from risk.behaviorgpt.ranker import Ranker
from risk.ingest.load_logs import load_action_vocab
from risk.scoring.exposure import P_AT, add_dollars, agent_dollars
from risk.scoring.roles import ROLES, role_flags
from risk.scoring.severity import severity

DECISIONS = ("run", "log", "review", "pause")
FLAG_WEIGHT = 0.5  # risk added per broken control, times the step's severity


@dataclass(frozen=True)
class Thresholds:
    log: float = 0.2
    review: float = 0.35
    pause: float = 0.5

    def decide(self, risk: float) -> str:
        if risk >= self.pause:
            return "pause"
        if risk >= self.review:
            return "review"
        if risk >= self.log:
            return "log"
        return "run"


def worst(decisions) -> str:
    return max(decisions, key=DECISIONS.index, default="run")


class SurpriseSeverityDetector:
    def __init__(
        self,
        ranker: Ranker | None,
        *,
        temperature: float = 1.0,
        epsilon: float = 0.02,
        thresholds: Thresholds = Thresholds(),
        context: dict[str, tuple[str, ...]] | None = None,
        role_rules: bool = True,
    ):
        self.ranker = ranker
        self.role_rules = role_rules
        base = "behaviorgpt" if ranker else "severity"
        self.name = f"{base}+roles" if role_rules else ("behaviorgpt" if ranker else "severity_only")
        self.temperature = temperature
        self.epsilon = epsilon
        self.thresholds = thresholds
        self.context = context or {}  # agent_type -> item ids of good runs to prime with
        self.fit_report: dict | None = None

    def info(self) -> dict:
        return {
            "name": self.name,
            "temperature": self.temperature,
            "thresholds": asdict(self.thresholds),
            "primed_actions": {t: len(c) for t, c in self.context.items()},
            "role_rules": self.role_rules,
            "p_unsafe_at": P_AT,
            "review_cost_usd": {t: r.review_cost_usd for t, r in ROLES.items()},
            "fit": self.fit_report,
        }

    # -- agent and session ------------------------------------------------------

    def score_agent(self, agent_id: str, sessions: dict[str, list[dict]]) -> dict:
        scored = {sid: self.score_session(events) for sid, events in sessions.items()}
        runs = [s["risk"] for s in scored.values()]
        decision = worst(s["decision"] for s in scored.values())
        return {
            "agent_id": agent_id,
            "status": "scored",
            "detector": self.name,
            "risk": max(runs),
            "mean_session_risk": round(sum(runs) / len(runs), 3),
            "level": {"pause": "high", "review": "medium"}.get(decision, "low"),
            "flagged": decision == "pause",
            "decision": decision,
            **agent_dollars(scored),
            "sessions": scored,
        }

    def score_session(self, events: list[dict], context: tuple[str, ...] | None = None) -> dict:
        """`context` overrides the priming runs, e.g. to leave a practice run out of its own."""
        if self.ranker:
            if context is None:
                context = self.context.get(events[0]["agent_type"], ())
            histories = [context + tuple(item_id(e) for e in events[:k]) for k in range(len(events))]
            rankings = self.ranker.scores_many(histories)
        else:
            rankings = [None] * len(events)
        steps = [
            self.score_step(e, r, role_flags(events, k) if self.role_rules else [])
            for k, (e, r) in enumerate(zip(events, rankings))
        ]
        dollars = add_dollars(events, steps, self.thresholds)
        top = max(steps, key=lambda s: s["risk"])
        return {
            "risk": top["risk"],
            "decision": worst(s["decision"] for s in steps),
            "riskiest_step": top["step"],
            "risk_without_flags": max(s["risk_without_flags"] for s in steps),
            "severity_only": max(s["severity"] for s in steps),
            "flags": sorted({f for s in steps for f in s["flags"]}),
            **dollars,
            "steps": steps,
        }

    # -- one step -------------------------------------------------------------

    def next_action_probs(
        self, agent_type: str, ranking: dict[str, float], temperature: float | None = None
    ) -> dict[str, float]:
        """p(next action) over the agent's own vocabulary, from BehaviorGPT's scores."""
        temperature = temperature or self.temperature
        vocab = sorted(load_action_vocab()[agent_type])
        scores = {a: ranking[i] for a in vocab if (i := item_id({"agent_type": agent_type, "action": a})) in ranking}
        uniform = 1 / len(vocab)
        if not scores:
            return {a: uniform for a in vocab}
        top = max(scores.values())
        weights = {a: math.exp((s - top) / temperature) for a, s in scores.items()}
        z = sum(weights.values())
        return {a: (1 - self.epsilon) * weights.get(a, 0.0) / z + self.epsilon * uniform for a in vocab}

    def score_step(
        self, event: dict, ranking: dict[str, float] | None, flags: list[tuple[str, str]] = ()
    ) -> dict:
        sev, why = severity(event)
        step = {"step": event["step"], "action": event["action"]}
        if ranking is None:  # no next-move model: surprise counts as 1
            surprise, said = 1.0, []
        else:
            probs = self.next_action_probs(event["agent_type"], ranking)
            expected = max(probs, key=probs.get)
            p, p_top = probs[event["action"]], probs[expected]
            surprise = 1 - p / p_top
            step |= {"p": round(p, 3), "expected": expected, "p_expected": round(p_top, 3)}
            said = [] if expected == event["action"] else [f"model expected {expected} ({p_top:.0%}), this was {p:.0%}"]
        base = surprise * sev
        risk = round(min(1.0, base + FLAG_WEIGHT * sev * len(flags)), 3)
        decision = self.thresholds.decide(risk)
        return step | {
            "surprise": round(surprise, 3),
            "severity": round(sev, 3),
            "risk": risk,
            "risk_without_flags": round(base, 3),
            "flags": [f for f, _ in flags],
            "decision": decision,
            "reason": "; ".join([f"{event['action']} (severity {sev:.2f})", *why, *(r for _, r in flags), *said]),
        }


def build_detector(
    *, severity_only: bool = False, role_rules: bool = True, practice_dir=None
) -> SurpriseSeverityDetector:
    """BehaviorGPT when UNBOXAI_API_KEY and UNBOXAI_CATALOG_ID are set, with the
    role rules; `severity_only` is the PRD baseline (no surprise, no rules).
    Fitted on `practice_dir` if it is a folder of good runs."""
    ranker = None
    if not severity_only and os.environ.get("UNBOXAI_API_KEY") and os.environ.get("UNBOXAI_CATALOG_ID"):
        from risk.behaviorgpt.ranker import BehaviorGPTRanker

        ranker = BehaviorGPTRanker()
    detector = SurpriseSeverityDetector(ranker, role_rules=role_rules and not severity_only)
    if practice_dir is not None and os.path.isdir(practice_dir):
        from risk.scoring.practice import fit, good_runs

        fit(detector, good_runs(practice_dir))
    return detector


def detector_from_env() -> SurpriseSeverityDetector:
    """The monitor's detector: fitted on RISK_PRACTICE_LOGS (default risk/eval/practice_logs)."""
    from risk.scoring.practice import DEFAULT_PRACTICE

    return build_detector(practice_dir=os.environ.get("RISK_PRACTICE_LOGS", DEFAULT_PRACTICE))
