"""The bank team's blind test (bank/testsets), as the dashboard shows it.

What we handed in: results/handin.json and results/scores_set_*.json, committed
before the answers were opened. Once the bank team puts the answer keys in
logs/answer_keys/ (bank/sim/reveal.py), the answers too, but only a key whose
SHA-256 matches its line in bank/testsets/seals.txt: a key changed after the seal
is reported, not shown. Display only: nothing here feeds the scoring.
"""

import hashlib
import json
import re
import statistics
from pathlib import Path

from risk.ingest.load_logs import REPO_ROOT

RESULTS = REPO_ROOT / "results"
SETS = REPO_ROOT / "bank" / "testsets" / "blind"
SEALS = REPO_ROOT / "bank" / "testsets" / "seals.txt"
KEYS = REPO_ROOT / "logs" / "answer_keys"
TENANT_PREFIX = "Test set "

SESSION = re.compile(r"^s_[^_]+_(?P<type>.+)_(?P<n>\d+)$")  # s_<run tag>_<agent type>_<nnn>


def tenant_name(set_name: str) -> str:
    """set_A -> "Test set A": the organisation a set is loaded into the monitor as."""
    return TENANT_PREFIX + set_name.removeprefix("set_")


def session_number(session_id: str) -> int | None:
    m = SESSION.match(session_id)
    return int(m["n"]) if m else None


def read_seals(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return dict(line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def answers(set_name: str, sealed: str | None, keys: Path) -> dict:
    """The answer key for one set, if it is there and matches its seal."""
    path = keys / f"{set_name}_labels.json"
    if not path.exists():
        return {"status": "sealed", "seal": sealed}
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != sealed:
        return {"status": "mismatch", "seal": sealed, "sha256": digest}
    key = json.loads(raw)
    bad_by_type: dict[str, list[int]] = {}
    for sid, bad in key["session_labels"].items():
        m = SESSION.match(sid)
        if bad and m:
            bad_by_type.setdefault(m["type"], []).append(int(m["n"]))
    rogue = {}
    for d in key["drifters"]:
        numbers = sorted(bad_by_type.get(d["agent_type"], []))
        rogue[d["agent_id"]] = {"first_rogue_n": numbers[0] if numbers else None, "rogue_sessions": numbers}
    return {"status": "revealed", "seal": sealed, "sha256": digest, "rogue": rogue,
            "rogue_sessions_by_type": {t: sorted(n) for t, n in bad_by_type.items()}}


def verdict(called: bool, truth: dict | None) -> str:
    if called:
        return "found" if truth else "false_alarm"
    return "missed" if truth else "clean"


def blind_test(results: Path = RESULTS, seals: Path = SEALS, keys: Path = KEYS) -> dict:
    handin_path = results / "handin.json"
    if not handin_path.exists():
        return {"available": False}
    handin = json.loads(handin_path.read_text())
    sealed = read_seals(seals)
    out_sets = []
    for set_name, calls in sorted(handin["sets"].items()):
        scores_path = results / f"scores_{set_name}.json"
        report = json.loads(scores_path.read_text()) if scores_path.exists() else {"traces": [], "provenance": {}}
        called = {e["agent_id"]: ("rogue", e) for e in calls["rogue"]} | {e["agent_id"]: ("watch", e) for e in calls["watch"]}
        ans = answers(set_name, sealed.get(set_name), keys)
        agents: dict[str, dict] = {}
        for t in report["traces"]:
            a = agents.setdefault(t["agent_id"], {"agent_id": t["agent_id"], "agent_type": t["agent_type"], "sessions": []})
            a["sessions"].append({
                "n": session_number(t["session_id"]), "session_id": t["session_id"], "decision": t["decision"],
                "risk": t["risk"], "first_risky_step": t["first_risky_step"], "categories": t["categories"],
                "risky": t["risky"],
            })
        for a in agents.values():
            a["sessions"].sort(key=lambda s: (s["n"] or 0, s["session_id"]))
            a["risky_sessions"] = sum(s["risky"] for s in a["sessions"])
            base = handin["baseline_by_type"].get(a["agent_type"], {})
            a["usual_risky_sessions"] = base.get("median_risky_sessions")
            call, entry = called.get(a["agent_id"], (None, None))
            a["call"] = call
            a["first_rogue_session"] = entry["first_rogue_session"] if call == "rogue" else None
            a["first_rogue_n"] = session_number(a["first_rogue_session"]) if a["first_rogue_session"] else None
            if ans["status"] == "revealed":
                truth = ans["rogue"].get(a["agent_id"])
                a["truth"] = {
                    "rogue": truth is not None,
                    "first_rogue_n": truth["first_rogue_n"] if truth else None,
                    "rogue_sessions": ans["rogue_sessions_by_type"].get(a["agent_type"], []),
                    "verdict": verdict(call == "rogue", truth),
                }
                if call == "rogue" and truth and truth["first_rogue_n"] and a["first_rogue_n"]:
                    a["truth"]["sessions_late"] = a["first_rogue_n"] - truth["first_rogue_n"]
        # called rogue first, then watched, then the rest
        rank = {"rogue": 0, "watch": 1, None: 2}
        listed = sorted(agents.values(), key=lambda a: (rank[a["call"]], a["agent_type"]))
        out_sets.append({
            "set": set_name, "letter": set_name.removeprefix("set_"), "tenant": tenant_name(set_name),
            "sessions": len(report["traces"]), "risky_sessions": sum(a["risky_sessions"] for a in listed),
            "called_rogue": [a["agent_id"] for a in listed if a["call"] == "rogue"],
            "watch": [a["agent_id"] for a in listed if a["call"] == "watch"],
            "answers": {k: v for k, v in ans.items() if k not in ("rogue", "rogue_sessions_by_type")},
            "agents": listed, "logs": report.get("provenance", {}).get("logs", {}),
        })
    return {
        "available": True,
        "model_commit": handin.get("model_commit"),
        "labels_read": handin.get("labels_read"),
        "rule": handin.get("rule"),
        "sets": out_sets,
        "score": score(out_sets),
    }


def score(sets: list[dict]) -> dict:
    """How the hand-in did, over the sets whose answers are revealed."""
    revealed = [s for s in sets if s["answers"]["status"] == "revealed"]
    agents = [a for s in revealed for a in s["agents"]]
    verdicts = [a["truth"]["verdict"] for a in agents]
    late = [a["truth"]["sessions_late"] for a in agents if "sessions_late" in a.get("truth", {})]
    return {
        "sets_revealed": len(revealed), "sets": len(sets),
        "mismatched": [s["set"] for s in sets if s["answers"]["status"] == "mismatch"],
        "rogue": verdicts.count("found") + verdicts.count("missed"),
        "found": verdicts.count("found"), "missed": verdicts.count("missed"),
        "false_alarms": verdicts.count("false_alarm"),
        "sets_right": sum(all(a["truth"]["verdict"] in ("found", "clean") for a in s["agents"]) for s in revealed),
        "median_sessions_off": statistics.median(abs(x) for x in late) if late else None,
    }
