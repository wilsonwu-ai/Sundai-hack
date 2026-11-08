"""Score .jsonl logs offline, then check the scores against the labels.

    python -m risk.scoring.run                       # shared/sample_logs, BehaviorGPT if configured
    python -m risk.scoring.run path/to/logs --steps  # print every step
    python -m risk.scoring.run --severity-only       # the baseline, no API calls
    python -m risk.scoring.run --no-practice         # unprimed, default temperature and thresholds
    python -m risk.scoring.run --out scores.json

The detector is fitted on the practice set first (risk/eval/practice_logs by
default; --practice to change it). Scores come from the label-free sessions;
the labels are read only afterwards, for AUC.
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from risk.ingest.load_logs import DEFAULT_LOG_DIR, load_logs
from risk.scoring.detector import sessions_by_agent
from risk.scoring.practice import DEFAULT_PRACTICE
from risk.scoring.surprise_severity import build_detector


def auc(scores: dict[str, float], labels: dict[str, bool]) -> float | None:
    """P(a random bad session scores above a random good one); ties count half."""
    bad = [scores[s] for s, y in labels.items() if y and s in scores]
    good = [scores[s] for s, y in labels.items() if not y and s in scores]
    if not bad or not good:
        return None
    wins = sum((b > g) + 0.5 * (b == g) for b in bad for g in good)
    return wins / (len(bad) * len(good))


def score_folder(folder, detector) -> dict:
    sequences, _ = load_logs(folder)  # labels deliberately not read here
    return {aid: detector.score_agent(aid, s) for aid, s in sessions_by_agent(sequences).items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", nargs="?", default=DEFAULT_LOG_DIR)
    parser.add_argument("--severity-only", action="store_true")
    parser.add_argument("--practice", type=Path, default=DEFAULT_PRACTICE, help="folder of good runs to fit on")
    parser.add_argument("--no-practice", action="store_true")
    parser.add_argument("--steps", action="store_true", help="print every step")
    parser.add_argument("--out", type=Path, help="write the scores as JSON")
    args = parser.parse_args()

    load_dotenv()
    practice = None if args.no_practice else args.practice
    if practice is not None:
        overlap = set(load_logs(practice)[1]) & set(load_logs(args.folder)[1])
        if overlap:
            raise SystemExit(f"practice and scored logs share sessions {sorted(overlap)}: keep them apart")
    detector = build_detector(severity_only=args.severity_only, practice_dir=practice)
    agents = score_folder(args.folder, detector)
    _, labels = load_logs(args.folder)

    info = detector.info()
    print(f"detector: {info['name']}, temperature {info['temperature']}, thresholds {info['thresholds']}")
    if info["fit"]:
        print(f"fitted on {info['fit']['practice_runs']} practice runs; primed actions per agent type: {info['primed_actions']}")
    print()
    for aid, agent in agents.items():
        print(f"{aid}  risk {agent['risk']:.2f}  {agent['level']}  ({agent['decision']})")
        for sid, s in agent["sessions"].items():
            label = "pressure" if labels.get(sid) else "normal"
            top = max(s["steps"], key=lambda x: x["risk"])
            print(f"  {sid} [{label:8}] risk {s['risk']:.2f} {s['decision']:6}  step {top['step']}: {top['reason']}")
            if args.steps:
                for st in s["steps"]:
                    print(f"      {st['step']:>2} {st['action']:<18} surprise {st['surprise']:.2f} x severity {st['severity']:.2f} = {st['risk']:.2f} {st['decision']}")
        print()

    sessions = {sid: s for a in agents.values() for sid, s in a["sessions"].items()}
    fmt = lambda x: "n/a" if x is None else f"{x:.2f}"  # noqa: E731
    rows = {detector.name: {sid: s["risk"] for sid, s in sessions.items()}}
    if detector.name != "severity_only":
        rows["surprise only"] = {sid: max(st["surprise"] for st in s["steps"]) for sid, s in sessions.items()}
        rows["severity-only baseline"] = {sid: s["severity_only"] for sid, s in sessions.items()}
    print(f"AUC over {len(sessions)} sessions (labels used only here): "
          + ", ".join(f"{name} {fmt(auc(scores, labels))}" for name, scores in rows.items()))

    if args.out:
        args.out.write_text(json.dumps(agents, indent=2))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
