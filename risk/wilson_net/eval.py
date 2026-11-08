"""Evaluate the network detector and the agent 10 scorer on sealed-label synthetic data.

Run from the repo root:

    python3 -m risk.wilson_net.eval --seeds 1 2 3 4 5 --sessions 1500

Writes risk/wilson_net/results/eval.json, eval.md and hero_decisions.json.
Labels are used only to split the data and to score the detector afterwards; the
detector never sees labels or pressure_mode.

Split per seed: of the normal client sessions (attack none), 60% train and 20%
validation; everything else (the other normal sessions and every attack) is
test. Fit on train, calibrate on validation, score test online. Agent 10: normal
behaviour is the good variant's, on any traffic (a good agent that revokes a
stolen token is behaving normally); 60% of its sessions train, 20% validation.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import random
import sys
from pathlib import Path

from risk.wilson_cs.eval import aggregate, auc, metrics_from_outcomes, rate, session_outcomes
from risk.wilson_net.agent_scorer import AgentScorer
from risk.wilson_net.detector import DECISION_ORDER, GROUPS, STRONG, NetDetector, _epoch

RESULTS = Path(__file__).resolve().parent / "results"

ROWS = [
    ("N0_o2", dict(order=2, group="N0")),
    ("N1_o2", dict(order=2, group="N1")),
    ("N2_o2", dict(order=2, group="N2")),
    ("N3_o1", dict(order=1, group="N3")),
    ("N3_o2", dict(order=2, group="N3")),
    ("N3_o3", dict(order=3, group="N3")),
    ("rules_only", dict(order=2, group="N3", mode="rules_only")),
    ("severity_only", dict(order=2, group="N3", mode="severity_only")),
]
HEADLINE = "N3_o2"


def _sid(s):
    return s[0]["session_id"]


def _fin(x):
    return x if isinstance(x, (int, float)) and math.isfinite(x) else None


# ---------------------------------------------------------------- data split
def make_split(sessions, labels, seed):
    normal = [s for s in sessions if labels[_sid(s)]["attack"] == "none"]
    rng = random.Random(seed)
    rng.shuffle(normal)
    n_train, n_val = int(0.6 * len(normal)), int(0.2 * len(normal))
    train, val = normal[:n_train], normal[n_train:n_train + n_val]
    used = {_sid(s) for s in train + val}
    return {"train": train, "val": val, "test": [s for s in sessions if _sid(s) not in used]}


def make_agent_split(netsec, nlabels, labels, seed):
    normal = [s for s in netsec
              if nlabels[s[0]["session_id"]]["agent_variant"] == "good"]
    rng = random.Random(seed)
    rng.shuffle(normal)
    n_train, n_val = int(0.6 * len(normal)), int(0.2 * len(normal))
    train, val = normal[:n_train], normal[n_train:n_train + n_val]
    used = {s[0]["session_id"] for s in train + val}
    return {"train": train, "val": val, "test": [s for s in netsec if s[0]["session_id"] not in used]}


def strip_label(events):
    return [{k: v for k, v in e.items() if k != "pressure_mode"} for e in events]


# ------------------------------------------------------------------ outcomes
def net_outcome(scored, sess, label) -> dict:
    rows = scored["requests"]
    dec = [r["decision"] for r in rows]
    strong = [i for i, d in enumerate(dec) if d in STRONG]
    paged = [i for i, d in enumerate(dec) if d == "page_it"]
    alert = [i for i, d in enumerate(dec) if d != "allow"]
    pos = {r.get("request_index", i): i for i, r in enumerate(sess)}
    hidx = label.get("harmful_request_index")
    hpos = pos.get(hidx) if hidx is not None else None
    out = {
        "attack": label.get("attack", "none"), "is_attack": label.get("attack", "none") != "none",
        "harmful": bool(label.get("harmful")) and hpos is not None,
        "client_type": label.get("client_type"), "asset": label.get("asset"),
        "value_usd": float(label.get("value_at_risk_usd") or 0.0),
        "score": scored["risk"], "any_strong": bool(strong), "any_page": bool(paged),
        "n_pages": 1 if paged else 0, "max_exposure_usd": scored["exposure_usd"],
    }
    if out["harmful"]:
        out["caught"] = any(i <= hpos for i in strong)
        out["page_caught"] = any(i <= hpos for i in paged)
        out["early"] = any(i < hpos for i in alert)
        fs = next((i for i in strong if i <= hpos), None)
        out["lead_requests"] = (hpos - fs) if fs is not None else None
        out["lead_seconds"] = (_epoch(sess[hpos]["ts"]) - _epoch(sess[fs]["ts"])) if fs is not None else None
    return out


def _mean(xs):
    return {"mean": (sum(xs) / len(xs)) if xs else None, "n": len(xs)}


def net_metrics(outs, attack_share: float | None = None) -> dict:
    n = len(outs)
    harm = [o for o in outs if o["harmful"]]
    normal = [o for o in outs if not o["is_attack"]]
    attacks = [o for o in outs if o["is_attack"]]
    m = {"n_sessions": n, "n_attack": len(attacks), "n_harmful": len(harm), "n_normal": len(normal)}
    m["session_auc"] = auc([o["score"] for o in attacks], [o["score"] for o in normal])
    m["catch_rate"] = rate(sum(o["caught"] for o in harm), len(harm))
    m["page_catch_rate"] = rate(sum(o["page_caught"] for o in harm), len(harm))
    m["early_warning"] = rate(sum(o["early"] for o in harm), len(harm))
    m["lead_requests"] = _mean([o["lead_requests"] for o in harm if o.get("lead_requests") is not None])
    m["lead_seconds"] = _mean([o["lead_seconds"] for o in harm if o.get("lead_seconds") is not None])
    m["false_alarm_rate"] = rate(sum(o["any_strong"] for o in normal), len(normal))
    agg = [o for o in normal if o["client_type"] == "aggregator"]
    m["false_alarm_aggregator"] = rate(sum(o["any_strong"] for o in agg), len(agg))
    by_attack = {}
    for a in sorted({o["attack"] for o in attacks}):
        sub = [o for o in attacks if o["attack"] == a]
        sh = [o for o in sub if o["harmful"]]
        by_attack[a] = {
            "n_sessions": len(sub), "n_harmful": len(sh),
            "catch_rate": rate(sum(o["caught"] for o in sh), len(sh)),
            "page_catch_rate": rate(sum(o["page_caught"] for o in sh), len(sh)),
            "early_warning": rate(sum(o["early"] for o in sh), len(sh)),
            "any_contain_rate": rate(sum(o["any_strong"] for o in sub), len(sub)),
            "any_page_rate": rate(sum(o["any_page"] for o in sub), len(sub)),
        }
    m["by_attack"] = by_attack
    # triage: one page per session (an incident), against paging on every alert (any session
    # contained or paged). Per-1,000 figures are weighted to the generator's mix (attack_share),
    # because the test split holds every attack but only a fifth of the normal sessions.
    def per_1000(key):
        if not outs:
            return None
        if attack_share is None or not normal or not attacks:
            return 1000.0 * sum(o[key] for o in outs) / n
        return 1000.0 * ((1 - attack_share) * sum(o[key] for o in normal) / len(normal)
                         + attack_share * sum(o[key] for o in attacks) / len(attacks))

    alerts_w, pages_w = per_1000("any_strong"), per_1000("any_page")
    alerts = sum(o["any_strong"] for o in outs)
    pages = sum(o["any_page"] for o in outs)
    stopped = sum(o["value_usd"] for o in harm if o["caught"])
    missed = sum(o["value_usd"] for o in harm if not o["caught"])
    by_asset = {}
    for a in sorted({o["asset"] for o in harm if o["asset"]}):
        sub = [o for o in harm if o["asset"] == a]
        by_asset[a] = {
            "n_harmful": len(sub), "caught": sum(o["caught"] for o in sub),
            "paged_before_harm": sum(o["page_caught"] for o in sub),
            "stopped_usd": sum(o["value_usd"] for o in sub if o["caught"]),
            "missed_usd": sum(o["value_usd"] for o in sub if not o["caught"]),
        }
    m["triage"] = {
        "pages_per_1000": pages_w,
        "baseline_pages_per_1000": alerts_w,
        "pages_avoided_share": (1.0 - pages_w / alerts_w) if alerts_w else None,
        "page_precision": rate(sum(1 for o in outs if o["any_page"] and o["is_attack"]), pages),
        "normal_pages_per_1000": 1000.0 * sum(o["any_page"] for o in normal) / len(normal) if normal else None,
        "exposure_stopped_usd": stopped, "exposure_missed_usd": missed,
        "by_asset": by_asset,
    }
    return m


# --------------------------------------------------------------------- core
def _score_net(det, sessions, labels):
    return [net_outcome(det.score_session(s), s, labels[_sid(s)]) for s in sessions]


def run_agent(gen: dict, split_seed: int) -> dict:
    netsec, nlabels = gen["netsec"], gen["netsec_labels"]
    sp = make_agent_split(netsec, nlabels, gen["traffic_labels"], split_seed)
    if not sp["train"] or not sp["test"]:
        return {}
    det = AgentScorer(order=2)
    det.fit([strip_label(s) for s in sp["train"]])
    cal = det.calibrate([strip_label(s) for s in sp["val"]])
    outs = [session_outcomes(det.score_session(strip_label(s)), nlabels[s[0]["session_id"]]) for s in sp["test"]]
    m = metrics_from_outcomes(outs)
    return {"metrics": m, "calibration": cal,
            "counts": {"train": len(sp["train"]), "val": len(sp["val"]), "test": len(sp["test"])}}


def run_seed(generate_fn, seed: int, n_sessions: int) -> dict:
    gen = generate_fn(n_sessions, seed)
    sessions, labels = gen["traffic"], gen["traffic_labels"]
    sp = make_split(sessions, labels, seed)
    counts = {"train": len(sp["train"]), "val": len(sp["val"]), "test": len(sp["test"]),
              "test_attack": sum(1 for s in sp["test"] if labels[_sid(s)]["attack"] != "none"),
              "test_harmful": sum(1 for s in sp["test"] if labels[_sid(s)]["harmful"]),
              "attack_share": sum(1 for s in sessions if labels[_sid(s)]["attack"] != "none") / max(1, len(sessions))}
    rows, calib = {}, {}
    for name, kw in ROWS:
        det = NetDetector(**kw)
        det.fit(sp["train"])
        c = det.calibrate(sp["val"])
        calib[name] = {k: (_fin(v) if isinstance(v, float) else v) for k, v in c.items()}
        rows[name] = net_metrics(_score_net(det, sp["test"], labels), counts["attack_share"])
    agent = run_agent(gen, seed)
    return {"seed": seed, "counts": counts, "calibration": calib, "rows": rows, "agent": agent}


# --------------------------------------------------------------------- hero
def _hero_net_rows(det, requests):
    sc = det.score_session(requests)
    rows = []
    for r in sc["requests"]:
        rows.append({k: r[k] for k in ("request_index", "route", "surprise", "severity", "flags", "risk", "exposure_usd", "decision")})
    return sc, rows


def hero_decisions(hero_fn, triage_fn, generate_fn, n_sessions: int, seed: int = 1) -> dict:
    gen = generate_fn(n_sessions, seed)
    sp = make_split(gen["traffic"], gen["traffic_labels"], seed)
    det = NetDetector(order=2, group="N3")
    det.fit(sp["train"])
    det.calibrate(sp["val"])
    asp = make_agent_split(gen["netsec"], gen["netsec_labels"], gen["traffic_labels"], seed)
    agent = AgentScorer(order=2)
    agent.fit([strip_label(s) for s in asp["train"]])
    agent.calibrate([strip_label(s) for s in asp["val"]])
    out = {"fit_seed": seed, "detector": "N3_o2",
           "pause_line": _fin(det.pause_line), "log_line": _fin(det.log_line),
           "agent_pause_line": _fin(agent.pause_line), "agent_log_line": _fin(agent.log_line),
           "cases": {}, "triage_cases": {}}
    if hero_fn is not None:
        for name, case in hero_fn().items():
            entry = {}
            traffic = case.get("traffic") or []
            if traffic:
                sc, rows = _hero_net_rows(det, traffic)
                entry["traffic"] = {"decision": sc["decision"], "risk": sc["risk"], "requests": rows}
            events = case.get("netsec") or []
            if events:
                sc = agent.score_session(strip_label(events))
                entry["netsec"] = {"decision": sc["decision"], "risk": sc["risk"], "steps": [
                    {k: s[k] for k in ("step", "action", "surprise", "severity", "flags", "risk", "decision")}
                    for s in sc["steps"]]}
            out["cases"][name] = entry
    if triage_fn is not None:
        for name, case in triage_fn().items():
            reqs = case.get("traffic") if isinstance(case, dict) else case
            sc, rows = _hero_net_rows(det, reqs)
            first = lambda d: next((r["request_index"] for r in rows if r["decision"] == d), None)  # noqa: E731
            decisive = next((i for i, r in enumerate(rows) if r["decision"] == "page_it"),
                            next((i for i, r in enumerate(rows) if r["decision"] in STRONG), 0))
            out["triage_cases"][name] = {
                "decision": sc["decision"], "risk": sc["risk"], "max_exposure_usd": sc["exposure_usd"],
                "first_contain_index": first("contain"), "first_page_index": first("page_it"),
                "n_requests": len(rows),
                "requests": rows[:max(60, decisive + 5)],
                "truncated": len(rows) > max(60, decisive + 5),
            }
    return out


# ------------------------------------------------------------ orchestration
def run_eval(generate_fn, seeds, n_sessions: int, out_dir=None, hero_fn=None, triage_fn=None, write: bool = True) -> dict:
    out_dir = Path(out_dir) if out_dir else RESULTS
    per_seed = [run_seed(generate_fn, s, n_sessions) for s in seeds]
    rows = {}
    for name, _ in ROWS:
        rows[name] = {
            "seeds": list(seeds),
            "per_seed": {str(ps["seed"]): ps["rows"][name] for ps in per_seed},
            "mean": aggregate([ps["rows"][name] for ps in per_seed]),
        }
    have = [ps["agent"]["metrics"] for ps in per_seed if ps.get("agent")]
    agent = {"mean": aggregate(have), "per_seed": {str(ps["seed"]): ps["agent"] for ps in per_seed if ps.get("agent")}} if have else {}
    result = {
        "synthetic": True, "seeds": list(seeds), "sessions_per_seed": n_sessions,
        "counts_per_seed": {str(ps["seed"]): ps["counts"] for ps in per_seed},
        "calibration_per_seed": {str(ps["seed"]): ps["calibration"] for ps in per_seed},
        "rows": rows, "agent10": agent,
    }
    hero = None
    if hero_fn is not None or triage_fn is not None:
        try:
            hero = hero_decisions(hero_fn, triage_fn, generate_fn, n_sessions, 1 if 1 in seeds else seeds[0])
            result["hero"] = {k: {layer: v["decision"] for layer, v in e.items()} for k, e in hero["cases"].items()}
            result["triage_cases"] = {k: v["decision"] for k, v in hero["triage_cases"].items()}
        except Exception as err:  # hero is optional; never sink the eval
            result["hero_error"] = f"{type(err).__name__}: {err}"
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        if hero is not None:
            (out_dir / "hero_decisions.json").write_text(json.dumps(hero, indent=1))
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


def _pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def _usd(x):
    return "n/a" if x is None else f"${x:,.0f}"


def render_md(res: dict) -> str:
    L = ["# Network detector and agent 10 scorer: evaluation (synthetic data)", ""]
    L.append(f"Seeds {res['seeds']}, {res['sessions_per_seed']} client sessions per seed. All data is synthetic, from the "
             "bank API traffic simulator; numbers describe this simulator, not real traffic. Rates are means over seeds with "
             "pooled counts (k/n) beside them. Positive class: attack sessions for AUC; harmful sessions for catch and lead.")
    L.append("")
    c = res["counts_per_seed"]
    L.append("Split per seed (train / validation / test, test attack, test harmful): " +
             "; ".join(f"seed {s}: {v['train']}/{v['val']}/{v['test']}, {v['test_attack']}, {v['test_harmful']}" for s, v in c.items()))
    L.append("")
    L.append("## Headline")
    L.append("")
    L.append("| Row | Session AUC | Catch (contained or paged at/before harm) | Early warning | Lead (requests) | Lead (seconds) | False alarm, normal | False alarm, aggregator | IT pages per 1,000 | Alerts per 1,000 (page-on-every-alert) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for name, row in res["rows"].items():
        m = row["mean"]
        L.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            name, _f(m["session_auc"]), _r(m["catch_rate"]), _r(m["early_warning"]),
            _f(m["lead_requests"]["mean"], 1), _f(m["lead_seconds"]["mean"], 1),
            _r(m["false_alarm_rate"]), _r(m["false_alarm_aggregator"]),
            _f(m["triage"]["pages_per_1000"], 1), _f(m["triage"]["baseline_pages_per_1000"], 1)))
    L.append("")
    L.append("Row names: `N<n>_o<k>` is the full detector (surprise x severity + flags, then the triage rule) on metadata group n at Markov order k. "
             "`rules_only` is a basic WAF: more than 120 requests a minute from one client, or more than 10 auth failures in 10 minutes from one address "
             "or for one subject, then contain; it never pages. `severity_only` scores risk = route severity. "
             "Calibration: at most 5% of normal validation sessions contained or paged, at most 25% logged or stronger; thresholds from normal training and validation sessions only.")
    L.append("")
    L.append("## Which metadata is needed (order 2, N0 to N3)")
    L.append("")
    L.append("| Group | Session AUC | Catch | Page before harm | Early warning | False alarm, normal | False alarm, aggregator |")
    L.append("|---|---|---|---|---|---|---|")
    for g in GROUPS:
        row = res["rows"].get(f"{g}_o2")
        if row:
            m = row["mean"]
            L.append(f"| {g} | {_f(m['session_auc'])} | {_r(m['catch_rate'])} | {_r(m['page_catch_rate'])} | {_r(m['early_warning'])} | {_r(m['false_alarm_rate'])} | {_r(m['false_alarm_aggregator'])} |")
    n0, n3 = res["rows"].get("N0_o2"), res["rows"].get("N3_o2")
    if n0 and n3 and None not in (n0["mean"]["session_auc"], n3["mean"]["session_auc"]):
        L.append("")
        L.append(f"N3 minus N0: session AUC {n3['mean']['session_auc'] - n0['mean']['session_auc']:+.3f}, catch rate "
                 f"{(n3['mean']['catch_rate']['rate'] or 0) - (n0['mean']['catch_rate']['rate'] or 0):+.2f}. "
                 "Read row by row: the group where catch stops improving without more false alarms is the metadata the network side needs.")
    L.append("")
    base = res["rows"].get(HEADLINE)
    if base:
        bm = base["mean"]
        L.append(f"## By attack ({HEADLINE})")
        L.append("")
        L.append("| Attack | Sessions | Harmful | Catch | Paged before harm | Early warning | Any contain | Any page |")
        L.append("|---|---|---|---|---|---|---|---|")
        for a, v in bm["by_attack"].items():
            L.append(f"| {a} | {v['n_sessions']:.0f} | {v['n_harmful']:.0f} | {_r(v['catch_rate'])} | {_r(v['page_catch_rate'])} | {_r(v['early_warning'])} | {_r(v['any_contain_rate'])} | {_r(v['any_page_rate'])} |")
        L.append("")
        L.append("## Triage: not every attack is an emergency")
        L.append("")
        L.append("Pages count one per session (one incident), only when the frozen rule says `page_it`. The baseline pages on every alert (any session contained or paged). "
        "Per-1,000 figures are weighted to the generator's traffic mix (attack share in eval.json counts_per_seed), not the attack-heavy test split. "
                 "Exposure is the sealed label value at risk of harmful sessions: stopped when contained or paged at or before the harmful request, otherwise missed. Means per seed.")
        L.append("")
        L.append("| Row | Pages per 1,000 | Baseline per 1,000 | Pages avoided | Page precision | Normal pages per 1,000 | Exposure stopped | Exposure missed |")
        L.append("|---|---|---|---|---|---|---|---|")
        for name, row in res["rows"].items():
            t = row["mean"]["triage"]
            L.append(f"| {name} | {_f(t['pages_per_1000'], 1)} | {_f(t['baseline_pages_per_1000'], 1)} | {_pct(t['pages_avoided_share'])} | {_r(t['page_precision'])} | {_f(t['normal_pages_per_1000'], 1)} | {_usd(t['exposure_stopped_usd'])} | {_usd(t['exposure_missed_usd'])} |")
        L.append("")
        L.append(f"### By asset ({HEADLINE}, mean per seed)")
        L.append("")
        L.append("| Asset | Harmful sessions | Caught | Paged before harm | Exposure stopped | Exposure missed |")
        L.append("|---|---|---|---|---|---|")
        for a, v in bm["triage"]["by_asset"].items():
            L.append(f"| {a} | {v['n_harmful']:.1f} | {v['caught']:.1f} | {v['paged_before_harm']:.1f} | {_usd(v['stopped_usd'])} | {_usd(v['missed_usd'])} |")
        L.append("")
    ag = res.get("agent10")
    if ag:
        m = ag["mean"]
        L.append("## Agent 10 behaviour (`agent_scorer`)")
        L.append("")
        L.append("| Measure | Value |")
        L.append("|---|---|")
        L.append(f"| Agent AUC, good against bad (max session score) | {_f(m['agent_auc_max'])} |")
        L.append(f"| Agent AUC (pause rate) | {_f(m['agent_auc_pause_rate'])} |")
        L.append(f"| Harmful steps paused at or before the first harmful step | {_r(m['catch_rate'])} |")
        L.append(f"| Early warning | {_r(m['early_warning'])} |")
        L.append(f"| Session AUC, harmful against not | {_f(m['session_auc'])} |")
        L.append(f"| False pauses on non-harmful sessions | {_r(m['false_alarm_session_rate'])} |")
        bv = m.get("by_variant", {}).get("bad")
        if bv:
            L.append(f"| Bad agent, harmful steps caught | {_r(bv['catch_rate'])} |")
        L.append("")
        L.append("Agent-level AUC is computed over about six agents per seed, so it is coarse.")
        L.append("")
    if "hero" in res:
        L.append("## Hero cases (fit and calibrated on seed 1)")
        L.append("")
        for k, v in res["hero"].items():
            L.append(f"- {k}: " + ", ".join(f"{layer} {d}" for layer, d in v.items()))
        for k, v in res.get("triage_cases", {}).items():
            L.append(f"- triage case {k}: {v}")
        L.append("")
        L.append("Per-request and per-step detail, with surprise and severity on every row, is in hero_decisions.json.")
        L.append("")
    elif "hero_error" in res:
        L.append(f"Hero cases were not written: {res['hero_error']}")
        L.append("")
    L.append("## Caveats")
    L.append("")
    L.append("- Synthetic data. The traffic generator and the detector were written to the same contract, so flags that mirror the "
             "attack definitions (new payee then transfer, limit jump, foreign object 200) will look strong here. Treat absolute numbers as a ceiling, not a forecast.")
    L.append("- Thresholds come from normal validation sessions only. The triage rule's non-threshold triggers (a 200 on a foreign object, "
             "exposure of $250,000 or more) can page regardless of the pause line; calibration counts them against the 5% budget.")
    L.append("- Dollar values are illustrative assumptions for the demo bank, not real figures. Exposure stopped and missed use the sealed label value at risk, "
             "and a contained session counts as stopped.")
    L.append("- Groups below N2 cannot see amounts or object ownership, so their value at risk uses per-route averages learned on normal training traffic and they cannot raise the foreign-object page.")
    L.append("- The N3 window also counts requests per minute per client (flag `volume`), which the contract's fan-out list does not name, so the scraper case can be contained.")
    L.append("- Beyond the contract's N3 list, the detector uses three network-specific rules: requests per minute per client (`volume`), long runs of one route "
             "(`repeat_run` at 25, `repeat_run_long` at 50; from N2 the gateway exempts partners that legitimately paginate), and privileged top-level body keys on a customer-record PATCH "
             "(`privileged_body_keys`, from `bank.body_keys`). Without them the scraper and mass-assignment cases cannot reach the pause line.")
    L.append("- Method choices made once, not tuned on the test split: the Markov base estimate is factored over token fields (P(route) x P(field | route)), "
             "interpolation weight 4, surprise normalised by 20 bits. The 5% budget is met on validation; test false alarms differ a little by sampling.")
    L.append("- The agent 10 scorer treats the good variant's sessions, on any traffic, as normal behaviour (a good agent revoking a stolen token is routine). "
             "Agent 10 scoring has about six agents per seed; the per-agent AUC is coarse.")
    L.append("")
    return "\n".join(L)


# ----------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--sessions", type=int, default=1500)
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    gen = importlib.import_module("bank.agents.netsec.generate").generate
    try:
        hero_mod = importlib.import_module("bank.agents.netsec.hero")
        hero_fn, triage_fn = hero_mod.hero_cases, getattr(hero_mod, "triage_cases", None)
    except Exception:
        hero_fn = triage_fn = None
    res = run_eval(gen, args.seeds, args.sessions, out_dir=args.out_dir, hero_fn=hero_fn, triage_fn=triage_fn)
    out = Path(args.out_dir) if args.out_dir else RESULTS
    print(f"wrote {out / 'eval.json'}, eval.md and hero_decisions.json; rows: {', '.join(res['rows'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
