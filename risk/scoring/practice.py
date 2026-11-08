"""Fit the detector on a practice set of good runs (PRD sections 7 and 8).

From the practice runs of each agent type, `fit` sets:

- the priming context: the last `k` good runs of that agent type, put before
  every session so BehaviorGPT reads the routine before the session it predicts;
- the temperature: the one under which the good runs are most likely
  (maximum likelihood over a grid), since BehaviorGPT's scores sit close together;
- the thresholds: pause is set so that at most `budget` (5%) of good runs get
  any pause; review and log are fractions of it, raised where needed so that at
  most 10% and 25% of good runs reach them. Each role gets its own
  line from its own good runs; a role with no practice runs uses the line set
  on all of them.

Every practice run is scored with the other practice runs as its context
(leave one out), never with itself.

risk/eval/practice_logs/ is the practice set: good runs of all 8 bank agents from
the bank team's simulator (bank.sim.run_sim --drifters 0, seeds 9001 and 9002,
none of them a blind test set's), and of the service and netsec agents from their
generators (seed 1). risk/eval/practice_handwritten/ is the first, hand-written
stand-in (4 agent types), kept for the tests.
"""

import math
from collections import defaultdict
from dataclasses import asdict

from risk.behaviorgpt.history import item_id
from risk.ingest.load_logs import REPO_ROOT, load_action_vocab, load_logs
from risk.scoring.surprise_severity import SurpriseSeverityDetector, Thresholds

DEFAULT_PRACTICE = REPO_ROOT / "risk" / "eval" / "practice_logs"
HANDWRITTEN_PRACTICE = REPO_ROOT / "risk" / "eval" / "practice_handwritten"
TEMPERATURES = (0.002, 0.003, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05, 0.075, 0.1, 0.2, 0.5, 1.0)
LOG_SHARE, REVIEW_SHARE = 0.4, 0.7
REVIEW_BUDGET, LOG_BUDGET = 0.10, 0.25  # at most this share of good runs reaches review / log


def good_runs(folder) -> dict[str, list[list[dict]]]:
    """agent_type -> its good runs, label stripped. Runs marked as pressure are dropped."""
    sequences, labels = load_logs(folder)
    runs = defaultdict(list)
    for s in sequences:
        if not labels[s[0]["session_id"]]:
            runs[s[0]["agent_type"]].append(s)
    return dict(runs)


MAX_CONTEXT = 40  # priming items at most: long runs would otherwise make very long histories


def context(runs: list[list[dict]], k: int, exclude: list[dict] | None = None) -> tuple[str, ...]:
    picked = [r for r in runs if r is not exclude][-k:]
    return tuple(item_id(e) for r in picked for e in r)[-MAX_CONTEXT:]


def fit(
    detector: SurpriseSeverityDetector,
    runs_by_type: dict[str, list[list[dict]]],
    *,
    k: int = 3,
    budget: float = 0.05,
) -> dict:
    report: dict = {
        "practice_runs": sum(len(r) for r in runs_by_type.values()),
        "by_agent_type": {t: len(r) for t, r in runs_by_type.items()},
    }

    if detector.ranker:
        cases = []  # (agent_type, the action taken, the ranking it was taken after)
        for agent_type, runs in runs_by_type.items():
            for run in runs:
                ctx = context(runs, k, exclude=run)
                histories = [ctx + tuple(item_id(e) for e in run[:j]) for j in range(len(run))]
                for event, ranking in zip(run, detector.ranker.scores_many(histories)):
                    cases.append((agent_type, event["action"], ranking))

        def log_likelihood(tau: float) -> float:
            return sum(math.log(detector.next_action_probs(t, r, tau)[a]) for t, a, r in cases)

        lls = {tau: log_likelihood(tau) for tau in TEMPERATURES}
        detector.temperature = max(lls, key=lls.get)
        detector.context = {t: context(runs, k) for t, runs in runs_by_type.items()}
        uniform = sum(math.log(1 / len(load_action_vocab()[t])) for t, _, _ in cases)
        report |= {
            "temperature": detector.temperature,
            "log_likelihood_per_step": round(lls[detector.temperature] / len(cases), 3),
            "uniform_log_likelihood_per_step": round(uniform / len(cases), 3),
        }

    by_type = {
        t: sorted(detector.score_session(run, context=context(runs, k, exclude=run))["risk"] for run in runs)
        for t, runs in runs_by_type.items()
    }
    scores = sorted(s for type_scores in by_type.values() for s in type_scores)
    # one line for all roles (the fallback), and one per role from its own good runs:
    # roles differ in what is normal (PRD open question), so each gets the same 5% budget
    detector.thresholds = pause_line(scores, budget)
    detector.thresholds_by_type = {t: pause_line(s, budget) for t, s in by_type.items()}
    report["thresholds"] = asdict(detector.thresholds)
    report["thresholds_by_type"] = {t: asdict(th) for t, th in detector.thresholds_by_type.items()}
    report["good_run_scores"] = scores
    report["good_run_scores_by_type"] = by_type
    detector.fit_report = report
    return report


def pause_line(scores: list[float], budget: float) -> Thresholds:
    """Pause just above the good runs, letting at most `budget` of them reach it.

    Review and log sit at 0.7 and 0.4 of it, but never so low that more than
    REVIEW_BUDGET (LOG_BUDGET) of the good runs reach them: where good runs crowd
    just under the pause line, a fixed share would send nearly all of them to
    review (round 1: every good sales, collections and lending session)."""
    scores = sorted(scores)

    def above(share: float) -> float:
        return round(scores[-(math.floor(share * len(scores)) + 1)] + 0.001, 3)

    pause = above(budget)
    # capped at the line above: where good runs pile up on one score, a line just
    # under the pause line would catch the whole pile; equal lines leave no review band
    review = min(max(round(REVIEW_SHARE * pause, 3), above(REVIEW_BUDGET)), pause)
    log = min(max(round(LOG_SHARE * pause, 3), above(LOG_BUDGET)), review)
    return Thresholds(log=log, review=review, pause=pause)
