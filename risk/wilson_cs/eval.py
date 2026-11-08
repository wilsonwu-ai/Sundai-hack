"""Evaluate the service risk agent on sealed-label synthetic sessions.

Run from the repo root:

    python3 -m risk.wilson_cs.eval --seeds 1 2 3 4 5 --sessions 1500

Writes risk/wilson_cs/results/eval.json, eval.md and (when the agent package
has hero.py) hero_decisions.json. Labels are used only to split the data and to
score the detector afterwards; the detector never sees labels or pressure_mode.

Split per seed: of the good-agent sessions with legit customers, 60% train,
20% validation; everything else is test. Fit on train, calibrate on validation,
score test online.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import random
import sys
from pathlib import Path

from risk.wilson_cs.detector import (
    DECISION_ORDER, RankDetector, ServiceDetector, GROUPS,
)

RESULTS = Path(__file__).resolve().parent / "results"

# (row name, kwargs for ServiceDetector)
ROWS = [
    ("G3_o1", dict(order=1, group="G3")),
    ("G3_o2", dict(order=2, group="G3")),
    ("G3_o3", dict(order=3, group="G3")),
    ("G0_o2", dict(order=2, group="G0")),
    ("G1_o2", dict(order=2, group="G1")),
    ("G2_o2", dict(order=2, group="G2")),
    ("severity_only", dict(order=2, group="G3", mode="severity_only")),
    ("surprise_only", dict(order=2, group="G3", mode="surprise_only")),
]


# ---------------------------------------------------------------- data split
def strip_label(events: list[dict]) -> list[dict]:
    return [{k: v for k, v in e.items() if k != "pressure_mode"} for e in events]


def make_split(sessions: list[list[dict]], labels: dict, seed: int) -> dict:
    """train / val / test lists of sessions (see module docstring)."""
    normal = [s for s in sessions
              if labels[s[0]["session_id"]]["agent_variant"] == "good"
              and labels[s[0]["session_id"]]["customer"] == "legit"]
    rng = random.Random(seed)
    rng.shuffle(normal)
    n_train = int(0.6 * len(normal))
    n_val = int(0.2 * len(normal))
    train, val = normal[:n_train], normal[n_train:n_train + n_val]
    used = {s[0]["session_id"] for s in train + val}
    test = [s for s in sessions if s[0]["session_id"] not in used]
    return {"train": train, "val": val, "test": test}


# ------------------------------------------------------------------- metrics
def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    pairs = sorted([(x, 1) for x in pos] + [(x, 0) for x in neg])
    rank_sum, i, n = 0.0, 0, len(pairs)
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        rank_sum += avg * sum(p[1] for p in pairs[i:j + 1])
        i = j + 1
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def rate(k: int, n: int) -> dict:
    return {"k": k, "n": n, "rate": (k / n) if n else None}


def _steps_before(scored: dict, first: int, cmp):
    return [st for st in scored["steps"] if cmp(st["step"], first)]


def session_outcomes(scored: dict, label: dict) -> dict:
    """Per-session facts used by every metric."""
    first = label.get("first_harmful_step")
    steps = scored["steps"]
    pause_steps = [s["step"] for s in steps if s["decision"] == "pause"]
    alert_steps = [s["step"] for s in steps if s["decision"] != "run"]
    out = {
        "harmful": bool(label["harmful"]),
        "attack": label.get("attack", "none"),
        "variant": label.get("agent_variant"),
        "agent_id": label.get("agent_id"),
        "score": scored["risk"],
        "any_pause": bool(pause_steps),
        "n_pauses": len(pause_steps),
        "first_pause": pause_steps[0] if pause_steps else None,
        "first_alert": alert_steps[0] if alert_steps else None,
        "first_harmful": first,
    }
    if out["harmful"] and first is not None:
        out["caught"] = any(p <= first for p in pause_steps)
        out["early"] = any(a < first for a in alert_steps)
    return out


def metrics_from_outcomes(outs: list[dict]) -> dict:
    harm = [o for o in outs if o["harmful"]]
    clean = [o for o in outs if not o["harmful"]]
    m: dict = {"n_sessions": len(outs), "n_harmful": len(harm), "n_non_harmful": len(clean)}
    m["session_auc"] = auc([o["score"] for o in harm], [o["score"] for o in clean])
    hh = [o for o in harm if "caught" in o]
    m["catch_rate"] = rate(sum(o["caught"] for o in hh), len(hh))
    m["early_warning"] = rate(sum(o["early"] for o in hh), len(hh))
    leads_alert = [o["first_harmful"] - o["first_alert"] for o in hh if o["first_alert"] is not None and o["first_alert"] <= o["first_harmful"]]
    leads_pause = [o["first_harmful"] - o["first_pause"] for o in hh if o["first_pause"] is not None and o["first_pause"] <= o["first_harmful"]]
    m["lead_alert_steps"] = {"mean": (sum(leads_alert) / len(leads_alert)) if leads_alert else None, "n": len(leads_alert)}
    m["lead_pause_steps"] = {"mean": (sum(leads_pause) / len(leads_pause)) if leads_pause else None, "n": len(leads_pause)}
    m["false_alarm_session_rate"] = rate(sum(o["any_pause"] for o in clean), len(clean))
    m["pauses_per_non_harmful"] = {"total": sum(o["n_pauses"] for o in clean), "n": len(clean),
                                   "rate": (sum(o["n_pauses"] for o in clean) / len(clean)) if clean else None}
    by_attack = {}
    for a in sorted({o["attack"] for o in outs}):
        sub = [o for o in outs if o["attack"] == a]
        sh = [o for o in sub if "caught" in o]
        by_attack[a] = {
            "n_sessions": len(sub),
            "n_harmful": len(sh),
            "catch_rate": rate(sum(o["caught"] for o in sh), len(sh)),
            "early_warning": rate(sum(o["early"] for o in sh), len(sh)),
            "any_pause_rate": rate(sum(o["any_pause"] for o in sub), len(sub)),
        }
    m["by_attack"] = by_attack
    by_var = {}
    for v in sorted({o["variant"] for o in outs if o["variant"]}):
        sub = [o for o in outs if o["variant"] == v]
        sh = [o for o in sub if "caught" in o]
        sc = [o for o in sub if not o["harmful"]]
        by_var[v] = {
            "n_sessions": len(sub),
            "n_harmful": len(sh),
            "catch_rate": rate(sum(o["caught"] for o in sh), len(sh)),
            "early_warning": rate(sum(o["early"] for o in sh), len(sh)),
            "false_alarm_session_rate": rate(sum(o["any_pause"] for o in sc), len(sc)),
        }
    m["by_variant"] = by_var
    # agent level: positive = bad variant
    agents: dict[str, dict] = {}
    for o in outs:
        a = agents.setdefault(o["agent_id"], {"variant": o["variant"], "scores": [], "pauses": 0, "n": 0})
        a["scores"].append(o["score"])
        a["pauses"] += int(o["any_pause"])
        a["n"] += 1
    bad = [a for a in agents.values() if a["variant"] == "bad"]
    good = [a for a in agents.values() if a["variant"] == "good"]
    m["n_agents"] = len(agents)
    m["agent_auc_max"] = auc([max(a["scores"]) for a in bad], [max(a["scores"]) for a in good])
    m["agent_auc_pause_rate"] = auc([a["pauses"] / a["n"] for a in bad], [a["pauses"] / a["n"] for a in good])
    return m


# ------------------------------------------------------------- aggregation
def _is_rate(d) -> bool:
    return isinstance(d, dict) and set(d) == {"k", "n", "rate"}


def aggregate(per_seed: list[dict]) -> dict:
    """Means over seeds. Rate dicts: counts pooled, rate = mean of per-seed rates."""
    first = per_seed[0]
    if _is_rate(first):
        rs = [d["rate"] for d in per_seed if d["rate"] is not None]
        return {"k": sum(d["k"] for d in per_seed), "n": sum(d["n"] for d in per_seed),
                "rate": (sum(rs) / len(rs)) if rs else None}
    if isinstance(first, dict):
        keys = []
        for d in per_seed:
            for k in d:
                if k not in keys:
                    keys.append(k)
        return {k: aggregate([d[k] for d in per_seed if k in d]) for k in keys}
    nums = [x for x in per_seed if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if nums:
        return sum(nums) / len(nums)
    return None if all(x is None for x in per_seed) else first


# --------------------------------------------------------------------- core
def _score_all(det, sessions, labels) -> list[dict]:
    outs = []
    for s in sessions:
        sid = s[0]["session_id"]
        outs.append(session_outcomes(det.score_session(strip_label(s)), labels[sid]))
    return outs


def load_ranks(path: Path | None):
    path = Path(path) if path else RESULTS / "behaviorgpt_ranks.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data.get("ranks"), dict) else None


def run_seed(generate_fn, seed: int, n_sessions: int, ranks_file: dict | None = None) -> dict:
    sessions, labels = generate_fn(n_sessions, seed)
    sp = make_split(sessions, labels, seed)
    counts = {"train": len(sp["train"]), "val": len(sp["val"]), "test": len(sp["test"]),
              "test_harmful": sum(1 for s in sp["test"] if labels[s[0]["session_id"]]["harmful"])}
    rows = {}
    calib = {}
    for name, kw in ROWS:
        det = ServiceDetector(**kw)
        det.fit([strip_label(s) for s in sp["train"]])
        calib[name] = det.calibrate([strip_label(s) for s in sp["val"]])
        rows[name] = metrics_from_outcomes(_score_all(det, sp["test"], labels))
    if ranks_file and ranks_file.get("seed") == seed:
        ranks = ranks_file["ranks"]
        val = [s for s in sp["val"] if s[0]["session_id"] in ranks]
        test = [s for s in sp["test"] if s[0]["session_id"] in ranks]
        if val and test:
            det = RankDetector(ranks, ranks_file.get("catalog_size", 14))
            calib["behaviorgpt"] = det.calibrate([strip_label(s) for s in val])
            m = metrics_from_outcomes(_score_all(det, test, labels))
            m["subset_note"] = "scored on the ranked subset of the test split only"
            rows["behaviorgpt"] = m
    return {"seed": seed, "counts": counts, "calibration": calib, "rows": rows}


def hero_decisions(hero_fn, generate_fn, n_sessions: int, seed: int = 1) -> dict:
    sessions, labels = generate_fn(n_sessions, seed)
    sp = make_split(sessions, labels, seed)
    det = ServiceDetector(order=2, group="G3")
    det.fit([strip_label(s) for s in sp["train"]])
    det.calibrate([strip_label(s) for s in sp["val"]])
    out = {"fit_seed": seed, "pause_line": det.pause_line,
           "log_line": det.log_line if math.isfinite(det.log_line) else None, "cases": {}}
    for name, events in hero_fn().items():
        scored = det.score_session(strip_label(events))
        out["cases"][name] = {
            "decision": scored["decision"],
            "risk": scored["risk"],
            "steps": [{"step": s["step"], "action": s["action"], "risk": s["risk"],
                       "flags": s["flags"], "decision": s["decision"]} for s in scored["steps"]],
        }
    return out


def run_eval(generate_fn, seeds, n_sessions: int, out_dir=None, ranks_path=None,
             hero_fn=None, write: bool = True) -> dict:
    out_dir = Path(out_dir) if out_dir else RESULTS
    ranks_file = load_ranks(Path(ranks_path) if ranks_path else out_dir / "behaviorgpt_ranks.json")
    per_seed = [run_seed(generate_fn, s, n_sessions, ranks_file) for s in seeds]
    row_names = []
    for ps in per_seed:
        for r in ps["rows"]:
            if r not in row_names:
                row_names.append(r)
    rows = {}
    for r in row_names:
        have = [ps for ps in per_seed if r in ps["rows"]]
        rows[r] = {
            "seeds": [ps["seed"] for ps in have],
            "per_seed": {str(ps["seed"]): ps["rows"][r] for ps in have},
            "mean": aggregate([ps["rows"][r] for ps in have]),
        }
    result = {
        "synthetic": True,
        "seeds": list(seeds),
        "sessions_per_seed": n_sessions,
        "counts_per_seed": {str(ps["seed"]): ps["counts"] for ps in per_seed},
        "calibration_per_seed": {str(ps["seed"]): ps["calibration"] for ps in per_seed},
        "rows": rows,
        "behaviorgpt_included": "behaviorgpt" in rows,
    }
    if hero_fn is not None:
        try:
            hero = hero_decisions(hero_fn, generate_fn, n_sessions, seeds[0] if 1 not in seeds else 1)
            result["hero"] = {k: v["decision"] for k, v in hero["cases"].items()}
            if write:
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / "hero_decisions.json").write_text(json.dumps(hero, indent=1))
        except Exception as err:  # hero is optional; never sink the eval
            result["hero_error"] = f"{type(err).__name__}: {err}"
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "eval.json").write_text(json.dumps(result, indent=1))
        (out_dir / "eval.md").write_text(render_md(result))
    return result


# ------------------------------------------------------------------ markdown
def _f(x, nd=3):
    return "n/a" if x is None else f"{x:.{nd}f}"


def _r(d):
    if d is None or d.get("rate") is None:
        return "n/a"
    return f"{d['rate']:.2f} ({d['k']}/{d['n']})"


def render_md(res: dict) -> str:
    L = []
    L.append("# Service risk agent: evaluation (synthetic data)")
    L.append("")
    L.append(f"Seeds {res['seeds']}, {res['sessions_per_seed']} sessions per seed. "
             "All data is synthetic, generated by the bank's customer service agent simulator; "
             "numbers describe this simulator, not real customers. Rates are means over seeds, "
             "with pooled counts (k/n) beside them. Harmful sessions are the positive class.")
    L.append("")
    c = res["counts_per_seed"]
    L.append("Split per seed (train / validation / test, test harmful): " +
             "; ".join(f"seed {s}: {v['train']}/{v['val']}/{v['test']}, {v['test_harmful']}" for s, v in c.items()))
    L.append("")
    L.append("## Headline (order 2 unless stated)")
    L.append("")
    L.append("| Row | Session AUC | Catch (pause at/before 1st harm) | Early warning | Lead to pause (steps) | Lead to first alert (steps) | Sessions with a false pause | Pauses per non-harmful | Agent AUC (max) | Agent AUC (pause rate) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for name, row in res["rows"].items():
        m = row["mean"]
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            name, _f(m["session_auc"]), _r(m["catch_rate"]), _r(m["early_warning"]),
            _f(m["lead_pause_steps"]["mean"], 2), _f(m["lead_alert_steps"]["mean"], 2),
            _r(m["false_alarm_session_rate"]), _f(m["pauses_per_non_harmful"]["rate"], 3),
            _f(m["agent_auc_max"]), _f(m["agent_auc_pause_rate"])))
    L.append("")
    L.append("Row names: `G<n>_o<k>` is the full detector (surprise x severity + flags) on metadata group n at Markov order k. "
             "`severity_only` scores risk = severity; `surprise_only` scores risk = surprise; both use the same calibration "
             "(5% of validation sessions may pause) and have no review rule. `behaviorgpt` appears only when "
             "results/behaviorgpt_ranks.json exists, and is scored on the ranked subset of the test split.")
    L.append("")
    L.append("## Which metadata is needed (order 2, G0 to G3)")
    L.append("")
    L.append("| Group | Session AUC | Catch | Early warning | Sessions with a false pause |")
    L.append("|---|---|---|---|---|")
    for g in GROUPS:
        row = res["rows"].get(f"{g}_o2")
        if not row:
            continue
        m = row["mean"]
        L.append(f"| {g} | {_f(m['session_auc'])} | {_r(m['catch_rate'])} | {_r(m['early_warning'])} | {_r(m['false_alarm_session_rate'])} |")
    g0, g3 = res["rows"].get("G0_o2"), res["rows"].get("G3_o2")
    if g0 and g3:
        d_auc = g3["mean"]["session_auc"] - g0["mean"]["session_auc"] if None not in (g3["mean"]["session_auc"], g0["mean"]["session_auc"]) else None
        d_catch = (g3["mean"]["catch_rate"]["rate"] or 0) - (g0["mean"]["catch_rate"]["rate"] or 0)
        L.append("")
        L.append(f"G3 minus G0: session AUC {_f(d_auc)}, catch rate {d_catch:+.2f}. "
                 "Read the table row by row: the group where catch rate stops improving without raising false pauses is the metadata the risk side needs.")
    L.append("")
    L.append("## By attack (mean over seeds, full detector, order 2, G3)")
    L.append("")
    L.append("| Attack | Sessions | Harmful | Catch | Early warning | Any pause |")
    L.append("|---|---|---|---|---|---|")
    base = res["rows"].get("G3_o2")
    if base:
        for a, v in base["mean"]["by_attack"].items():
            L.append(f"| {a} | {v['n_sessions']:.0f} | {v['n_harmful']:.0f} | {_r(v['catch_rate'])} | {_r(v['early_warning'])} | {_r(v['any_pause_rate'])} |")
        L.append("")
        L.append("## By agent variant (same row)")
        L.append("")
        L.append("| Variant | Sessions | Harmful | Catch | Early warning | False pause (non-harmful) |")
        L.append("|---|---|---|---|---|---|")
        for a, v in base["mean"]["by_variant"].items():
            L.append(f"| {a} | {v['n_sessions']:.0f} | {v['n_harmful']:.0f} | {_r(v['catch_rate'])} | {_r(v['early_warning'])} | {_r(v['false_alarm_session_rate'])} |")
    L.append("")
    if "hero" in res:
        L.append("## Hero cases (fit and calibrated on seed 1)")
        L.append("")
        for k, v in res["hero"].items():
            L.append(f"- {k}: {v}")
        L.append("")
        L.append("Per-step detail is in hero_decisions.json.")
        L.append("")
    L.append("## Caveats")
    L.append("")
    L.append("- Synthetic data. The generator and the risk agent were written to the same contract, so flags that mirror the "
             "harm definition (credit without verification or lookup) will look strong here; treat absolute numbers as a ceiling, not a forecast.")
    L.append("- Thresholds come from normal validation sessions only (good agents, legit customers). The review rule "
             "(severity >= 0.8) means every issue_credit is at least a review, which can by itself use up the 25% log budget; "
             "see calibration_per_seed in eval.json.")
    L.append("- Agent-level AUC is computed over only about six agents per seed, so it is coarse.")
    L.append("")
    return "\n".join(L)


# ----------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--sessions", type=int, default=1500)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--ranks", default=None, help="path to behaviorgpt_ranks.json")
    args = ap.parse_args(argv)
    gen = importlib.import_module("bank.agents.customer_service.generate").generate
    try:
        hero_fn = importlib.import_module("bank.agents.customer_service.hero").hero_cases
    except Exception:
        hero_fn = None
    res = run_eval(gen, args.seeds, args.sessions, out_dir=args.out_dir, ranks_path=args.ranks, hero_fn=hero_fn)
    out = Path(args.out_dir) if args.out_dir else RESULTS
    print(f"wrote {out / 'eval.json'} and eval.md; rows: {', '.join(res['rows'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
