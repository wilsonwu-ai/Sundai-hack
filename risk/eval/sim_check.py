"""Check the detector and the hand-in rule on simulator runs whose answers we have.

    python -m risk.scoring.report <run folder> --out <scores>/scores_<run>.json   # per run, with any model
    python -m risk.eval.sim_check <scores folder> <runs folder> [--out check.json]

A run folder is what `python -m bank.sim.run_sim --out <runs folder> --run-id <run>`
writes: events.jsonl and its answers, labels.json. Scoring reads only the events;
this check reads the answers afterwards. It applies the blind-test hand-in rule
(risk/scoring/handin.py) across the runs, as on the blind sets, and reports per
agent and per session: what we called against what was true, and how well the
session scores rank rogue sessions above aligned ones (AUC).

For tuning on runs of our own, never on a blind set.
"""

import argparse
import json
from pathlib import Path

from risk.eval.blind_confusion import matrix
from risk.scoring.handin import handin, session_number


def auc(pos: list[float], neg: list[float]) -> float | None:
    """P(a random positive scores above a random negative), ties count half."""
    if not pos or not neg:
        return None
    ranked = sorted([(s, 1) for s in pos] + [(s, 0) for s in neg])
    rank_sum, i = 0.0, 0
    while i < len(ranked):
        j = i
        while j < len(ranked) and ranked[j][0] == ranked[i][0]:
            j += 1
        mid = (i + j + 1) / 2  # average 1-based rank of the tie group
        rank_sum += mid * sum(lab for _, lab in ranked[i:j])
        i = j
    return round((rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)), 3)


def check(reports: dict[str, dict], answers: dict[str, dict]) -> dict:
    h = handin(reports)
    agents, sessions, offsets = [], [], []
    for run, report in reports.items():
        key = answers[run]
        rogue = {d["agent_id"] for d in key["drifters"]}
        calls = {e["agent_id"]: session_number(e["first_rogue_session"]) for e in h["sets"][run]["rogue"]}
        labels = key["session_labels"]
        for agent in sorted({t["agent_id"] for t in report["traces"]}):
            agents.append((agent in rogue, agent in calls))
            if agent in rogue and agent in calls:
                first = min(session_number(s) for s, bad in labels.items() if bad and f"_{agent.split('-')[0]}_" in s)
                offsets.append(calls[agent] - first)
        for t in report["traces"]:
            n = session_number(t["session_id"])
            sessions.append({"rogue": labels[t["session_id"]], "decision": t["decision"],
                             "handin": t["agent_id"] in calls and n >= calls[t["agent_id"]],
                             **{k: t.get(k) for k in ("risk", "risk_without_flags", "severity_only")}})

    def ranking(field):
        return auc([s[field] for s in sessions if s["rogue"] and s[field] is not None],
                   [s[field] for s in sessions if not s["rogue"] and s[field] is not None])

    return {
        "runs": sorted(reports),
        "agents": matrix(agents),
        "sessions_handin": matrix((s["rogue"], s["handin"]) for s in sessions),
        "sessions_flagged": matrix((s["rogue"], s["decision"] in ("review", "pause")) for s in sessions),
        "auc": {"risk": ranking("risk"), "risk_without_flags": ranking("risk_without_flags"),
                "severity_only": ranking("severity_only")},
        "first_rogue_session_offsets": offsets,
        "calls": {run: [(e["agent_id"], e["first_rogue_session"]) for e in s["rogue"]] for run, s in h["sets"].items()},
        "truth": {run: [(d["agent_id"], d["drift_start_session"] + 1, d["drift_level"]) for d in answers[run]["drifters"]]
                  for run in reports},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("scores", type=Path, help="folder of scores_<run>.json")
    parser.add_argument("runs", type=Path, help="folder of run folders, each with labels.json")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    reports = {p.stem.removeprefix("scores_"): json.loads(p.read_text()) for p in sorted(args.scores.glob("scores_*.json"))}
    answers = {run: json.loads((args.runs / run / "labels.json").read_text()) for run in reports}
    r = check(reports, answers)
    a, s, f = r["agents"], r["sessions_handin"], r["sessions_flagged"]
    print(f"runs: {', '.join(r['runs'])}")
    print(f"agents   TP {a['tp']} FN {a['fn']} FP {a['fp']} TN {a['tn']}  precision {a['precision']} recall {a['recall']}")
    print(f"sessions (hand-in)  TP {s['tp']} FN {s['fn']} FP {s['fp']} TN {s['tn']}  precision {s['precision']} recall {s['recall']}")
    print(f"sessions (flagged)  TP {f['tp']} FN {f['fn']} FP {f['fp']} TN {f['tn']}  precision {f['precision']} recall {f['recall']}")
    print(f"AUC {r['auc']}  first-session offsets {r['first_rogue_session_offsets']}")
    for run in r["runs"]:
        print(f"  {run}: called {r['calls'][run]}  truth {r['truth'][run]}")
    if args.out:
        args.out.write_text(json.dumps(r, indent=2))


if __name__ == "__main__":
    main()
