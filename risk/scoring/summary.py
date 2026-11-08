"""One page from several scores files: what was scored, by which model, and what is risky.

    python -m risk.scoring.summary results/scores_set_*.json --handin results/handin.json > results/README.md

Reads only the scores (risk/scoring/report.py output), never labels.
"""

import json
import sys
from pathlib import Path


def handin_section(h: dict) -> list[str]:
    lines = ["## Hand-in: rogue agents and the first session they were rogue in", "",
             "| Set | Rogue agent | First rogue session | Risky sessions (typical for its type) |", "|---|---|---|---|"]
    for set_name, s in h["sets"].items():
        label = set_name.replace("set_", "")
        if not s["rogue"]:
            lines.append(f"| {label} | none | | |")
        for e in s["rogue"]:
            lines.append(f"| {label} | `{e['agent_id']}` | `{e['first_rogue_session']}` | {e['risky_sessions']} of {e['sessions']} ({e['type_median']:g}) |")
    watch = [(n, e) for n, s in h["sets"].items() for e in s["watch"]]
    if watch:
        lines += ["", "Not handed in, but above normal: " + "; ".join(
            f"{n.replace('set_', 'set ')} `{e['agent_id']}` ({e['risky_sessions']} risky vs a typical {e['type_median']:g})" for n, e in watch) + "."]
    lines += ["", "How: " + " ".join(h["rule"].split()), ""]
    return lines


def summarize(paths: list[Path], handin: dict | None = None) -> str:
    reports = [(p, json.loads(p.read_text())) for p in sorted(paths)]
    commits = {r.get("provenance", {}).get("model_commit") for _, r in reports}
    detector = reports[0][1].get("detector") or {}
    lines = [
        "# Blind test scores",
        "",
        "Scored by the risk team before the answers were opened (PRD 6.4, step 5). "
        "No labels were read: `pressure_mode` is stripped by the loader before scoring, "
        "and every file records `labels_read: false`.",
        "",
        f"- Model commit: `{', '.join(sorted(c or '?' for c in commits))}`",
        f"- Detector: `{detector.get('name')}`, temperature {detector.get('temperature')}, "
        f"fitted on {(detector.get('fit') or {}).get('practice_runs')} practice runs",
        "- A trace is risky when its worst step was sent to review or paused; an agent when any of its traces is. "
        "Each file also has every trace's score (`risk`), the severity-only baseline (`severity_only`) "
        "and the flags-off score (`risk_without_flags`), for AUC once the labels are open.",
        "",
        *(handin_section(handin) if handin else []),
        "## Scores per set",
        "",
        "| Set | Traces | Risky traces | Paused | Review | Agents | Agents with a risky trace | Log SHA-256 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p, r in reports:
        c = r["counts"]
        logs = r.get("provenance", {}).get("logs", {})
        digest = ", ".join(f"`{h[:12]}…`" for h in logs.values())
        lines.append(
            f"| [{p.stem.replace('scores_', '')}]({p.name}) | {c['traces']} | {c['risky_traces']} | "
            f"{c['traces_by_decision']['pause']} | {c['traces_by_decision']['review']} | {c['agents']} | "
            f"{c['risky_agents']} | {digest} |"
        )
    for p, r in reports:
        lines += ["", f"## {p.stem.replace('scores_', '').replace('_', ' ')}", ""]
        risky_agents = [a for a in r["agents"] if a["risky"]]
        lines.append(f"Risky agents ({len(risky_agents)} of {len(r['agents'])}): " + (", ".join(
            f"`{a['agent_id']}` ({a['risky_traces']} of {a['traces']} traces, peak {a['risk']:.2f})" for a in risky_agents) or "none"))
        risky = [t for t in r["traces"] if t["risky"]]
        lines += ["", f"Risky traces ({len(risky)} of {len(r['traces'])}), riskiest first:", "",
                  "| Trace | Agent | Decision | Risk | From step | Categories |", "|---|---|---|---|---|---|"]
        for t in risky:
            lines.append(f"| `{t['session_id']}` | `{t['agent_id']}` | {t['decision']} | {t['risk']:.2f} | "
                         f"{t['first_risky_step']} | {', '.join(t['categories'])} |")
    lines += ["", "To compare once the answers are opened:", "", "```sh",
              "python -m risk.scoring.report bank/testsets/blind/set_A --labels <labels.json>   # per set", "```", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    args = sys.argv[1:]
    handin = None
    if "--handin" in args:
        i = args.index("--handin")
        handin = json.loads(Path(args[i + 1]).read_text())
        args = args[:i] + args[i + 2:]
    sys.stdout.write(summarize([Path(a) for a in args], handin))
