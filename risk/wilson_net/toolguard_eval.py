"""python3 -m risk.wilson_net.toolguard_eval --seeds 1 2 3 4 5 --sessions 1500

Writes risk/wilson_net/results/toolguard.json (frozen shape, read by the demo page) and toolguard.md.
The guard is fitted on a separate all-normal set (seed + 100); labels are read only to score, never to decide.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from bank.agents.netsec.toolcalls import ATTACK_ROWS, CASE_KEYS, HERO_META, generate_toolcalls, toolcall_hero_cases
from risk.wilson_net.toolguard import ToolGuard

RESULTS = Path(__file__).resolve().parent / "results"
STOP = ("hold", "block_page")
ORDER = {"allow": 0, "watch": 1, "hold": 2, "block_page": 3}
ROWS = ("guard", "http_layer_only", "keyword_screen")


def _ratio(k, n):
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else 0.0}


# Each method returns, per session, (list of per-call decisions, page keys). Pages are deduplicated by
# destination host within a seed, so the key is a host (or the agent id when the call has no destination).

def _guard_method(guard, sessions, labels):
    out = []
    for s in sessions:
        res = guard.score_session(s)
        decs = [r["decision"] for r in res]
        pages = {s[r["call_index"]]["bank.dest.host"] or s[r["call_index"]]["gen_ai.agent.id"]
                 for r in res if r["decision"] == "block_page"}
        out.append((decs, pages))
    return out


def _http_method(guard, sessions, labels):
    """The network layer sees inbound requests only. It blocks a session it can already see as part of a bot
    burst (label http_visible: the bot campaign after its first five sessions), before any tool call runs."""
    out = []
    for s in sessions:
        lab = labels[s[0]["gen_ai.conversation.id"]]
        if lab["http_visible"]:
            host = next((c["bank.dest.host"] for c in s if c["bank.dest.host"]), s[0]["gen_ai.agent.id"])
            out.append((["block_page"] * len(s), {host}))
        else:
            out.append((["allow"] * len(s), set()))
    return out


def _keyword_method(guard, sessions, labels):
    """A keyword list on the customer message. A hit holds the whole session for review (a content flag is a
    review queue, not an IT page), so it pages nobody. It cannot see tool calls."""
    out = []
    for s in sessions:
        c0 = s[0]
        hit = c0["bank.actor.kind"] == "customer_message" and c0["bank.message.injection_suspected"] is True
        out.append((["hold" if hit else "allow"] * len(s), set()))
    return out


METHODS = {"guard": _guard_method, "http_layer_only": _http_method, "keyword_screen": _keyword_method}


def _evaluate(decisions, sessions, labels):
    atk_k = atk_n = fb_k = fb_n = ls_k = ls_n = 0
    stopped = missed = 0.0
    pages = set()
    by_case = {r: [0, 0] for r in ATTACK_ROWS}
    for s, (decs, pg) in zip(sessions, decisions):
        lab = labels[s[0]["gen_ai.conversation.id"]]
        pages |= pg
        if lab["attack"] == "none":
            fb_n += 1
            blocked = any(d in STOP for d in decs)
            fb_k += blocked
            if any(c["bank.args.classes"] for c in s):
                ls_n += 1
                ls_k += blocked
        else:
            atk_n += 1
            ok = decs[lab["first_harmful_call"]] in STOP
            atk_k += ok
            by_case[lab["attack"]][0] += ok
            by_case[lab["attack"]][1] += 1
            if ok:
                stopped += lab["value_at_risk_usd"]
            else:
                missed += lab["value_at_risk_usd"]
    return {"atk": (atk_k, atk_n), "fb": (fb_k, fb_n), "ls": (ls_k, ls_n), "pages": len(pages),
            "n": len(sessions), "stopped": stopped, "missed": missed, "by_case": by_case}


def run(seeds, n_sessions):
    per = {r: [] for r in ROWS}
    case_tot = {r: [0, 0] for r in ATTACK_ROWS}
    seed1 = None
    guard1 = None
    for seed in seeds:
        train, _ = generate_toolcalls(n_sessions, seed + 100, mix={"normal": 1.0})
        guard = ToolGuard().fit(train)
        sessions, labels = generate_toolcalls(n_sessions, seed)
        for name in ROWS:
            res = _evaluate(METHODS[name](guard, sessions, labels), sessions, labels)
            per[name].append(res)
            if name == "guard":
                for r, (k, n) in res["by_case"].items():
                    case_tot[r][0] += k
                    case_tot[r][1] += n
        if seed1 is None:
            seed1, guard1 = (sessions, labels), guard

    rows = {}
    for name in ROWS:
        rs = per[name]

        def agg(key):
            k = sum(r[key][0] for r in rs)
            n = sum(r[key][1] for r in rs)
            rates = [r[key][0] / r[key][1] for r in rs if r[key][1]]
            return {"k": k, "n": n, "rate": round(sum(rates) / len(rates), 4) if rates else 0.0}

        rows[name] = {
            "blocked_before_run": agg("atk"),
            "false_blocks": agg("fb"),
            "legit_sensitive_blocked": agg("ls"),
            "pages_per_1000": round(sum(1000 * r["pages"] / r["n"] for r in rs) / len(rs), 2),
            "exposure_stopped_usd": round(sum(r["stopped"] for r in rs) / len(rs), 2),
            "exposure_missed_usd": round(sum(r["missed"] for r in rs) / len(rs), 2),
        }

    # the seven cases, scored by the guard fitted on seed 1's training set
    heroes = toolcall_hero_cases()
    hero_out, cases = {}, {}
    for key, calls in heroes.items():
        res = guard1.score_session(calls)
        if key in ("hero_legit", "hero_attack"):
            hero_out[key] = [{
                "call_index": r["call_index"], "tool": r["tool"], "agent_id": r["agent_id"],
                "dest_host": calls[r["call_index"]]["bank.dest.host"],
                "classes": calls[r["call_index"]]["bank.args.classes"],
                "masked_args": calls[r["call_index"]]["gen_ai.tool.call.arguments"],
                "score": round(r["score"], 4), "decision": r["decision"], "reasons": r["reasons"],
            } for r in res]
        first = next((r for r in res if r["decision"] in STOP), None)
        worst = max(res, key=lambda r: ORDER[r["decision"]])
        pick = first or worst
        cases[key] = {"decision": pick["decision"], "exposure_usd": pick["exposure_usd"], "actor": HERO_META[key]["actor"],
                      "first_block_index": first["call_index"] if first else None}

    sessions1, labels1 = seed1
    scores = guard1.agent_scores(sessions1)
    atype, hijacked = {}, set()
    for s in sessions1:
        for c in s:
            atype[c["gen_ai.agent.id"]] = c["gen_ai.agent.name"]
        if labels1[s[0]["gen_ai.conversation.id"]]["attack"] != "none":
            hijacked.add(s[0]["gen_ai.agent.id"])
    agents = [{"agent_id": a, "agent_type": atype[a], "score": round(sc, 4), "hijacked": a in hijacked}
              for a, sc in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]

    return {
        "sample": False, "seeds": list(seeds), "sessions_per_seed": n_sessions,
        "rows": rows,
        "by_case": {r: {"blocked_before_run": _ratio(*case_tot[r])} for r in ATTACK_ROWS},
        "hero": hero_out, "cases": cases, "agents": agents,
    }, guard1


def _pct(x):
    return f"{100 * x['rate']:.1f}% ({x['k']}/{x['n']})"


def write_md(res, guard, path):
    r = res["rows"]
    L = ["# Tool-call guard: evaluation (synthetic data)", ""]
    L.append(f"Seeds {res['seeds']}, {res['sessions_per_seed']} sessions per seed, 85% normal and 15% attacks over six rows. "
             "All data is synthetic from the tool-call simulator; numbers describe this simulator, not real agent traffic. "
             "Rates are means over seeds with pooled counts (k/n) beside them. The guard is fitted on a separate all-normal set "
             "(seed plus 100) and never reads labels or raw values. It flags calls and agents worth a human look and blocks "
             "the calls the rule names.")
    L += ["", "## Rows", "",
          "| Row | Blocked before run | False blocks | Legit sensitive blocked | IT pages per 1,000 | Exposure stopped (USD, mean per seed) | Exposure missed (USD, mean per seed) |",
          "|---|---|---|---|---|---|---|"]
    for name in ROWS:
        x = r[name]
        L.append(f"| {name} | {_pct(x['blocked_before_run'])} | {_pct(x['false_blocks'])} | {_pct(x['legit_sensitive_blocked'])} | "
                 f"{x['pages_per_1000']:.2f} | {x['exposure_stopped_usd']:,.0f} | {x['exposure_missed_usd']:,.0f} |")
    L += ["", "Blocked before run counts a `block_page` or a `hold` on the first harmful call, because neither call executes. "
          "False blocks are normal sessions with any `block_page` or `hold`. Legit sensitive blocked is the same count over normal "
          "sessions that carry a sensitive class to an approved destination (the KYC vendor getting an SSN, the card network "
          "getting a card number). Pages are deduplicated by destination host within a seed.", ""]
    L += ["## By case (guard)", "", "| Case | Blocked before run |", "|---|---|"]
    for k, v in res["by_case"].items():
        L.append(f"| {k} | {_pct(v['blocked_before_run'])} |")
    L += ["", "## The seven cases", "", "| Case | Who is behind it | Decision | First block index | Exposure (USD) |", "|---|---|---|---|---|"]
    for k in CASE_KEYS:
        c = res["cases"][k]
        L.append(f"| {k} | {c['actor']} | {c['decision']} | {c['first_block_index']} | {c['exposure_usd']:,.0f} |")
    L += ["", "## Baseline assumptions (chosen by the builder, stated so they can be challenged)", "",
          "- **http_layer_only.** Outbound tool calls never cross the inbound API, so the network layer sees none of them. "
          "The one place an attack also shows inbound is the bot campaign: the same client sends a burst of messages, so "
          "after its first five sessions the network layer blocks the rest of that campaign before any tool call runs. "
          "Every other attack is invisible to it. Pages are keyed by the campaign's destination host, so the burst is one page.",
          "- **keyword_screen.** A keyword list runs on the customer message and the result is carried as "
          "`bank.message.injection_suspected` on the session's first call (with `bank.actor.kind = customer_message`). "
          "Only attacks that arrive in a customer message can raise it: planted instruction and bot campaign at a 60% rate "
          "(some planted wording slips past a list), email-not-on-file at 25%. Poisoned documents, the unattested bot, and the "
          "lookalike drip carry no customer message, so the screen never fires on them. Ordinary customer messages raise the "
          "flag 3% of the time (false positives). A hit holds the whole session for review, which pages nobody, so its page "
          "count is zero by construction and its cost shows in false blocks.",
          "- **Fit.** The guard's per-agent-type sequence model is agent 9's Markov model (order 2) over tool names; the watch "
          f"line was calibrated on held-out normal sessions so at most 5% get a `watch` (seed 1 line: {guard.watch_line:.3f}).", ""]
    h = res["hero"]
    L += ["## Hero sessions (guard fitted on seed 1)", ""]
    for key in ("hero_legit", "hero_attack"):
        L += [f"### {key}", "", "| Call | Tool | Host | Classes | Score | Decision |", "|---|---|---|---|---|---|"]
        for c in h[key]:
            L.append(f"| {c['call_index']} | {c['tool']} | {c['dest_host'] or '-'} | {', '.join(c['classes']) or '-'} | {c['score']:.2f} | {c['decision']} |")
        L.append("")
    ag = res["agents"]
    L += ["## Agents (seed 1)", "",
          f"{len(ag)} agents; each carries the highest call score in its last 50 calls. Top of the list and every hijacked agent:", "",
          "| Agent | Type | Score | Hijacked |", "|---|---|---|---|"]
    shown = [a for a in ag if a["hijacked"]] + [a for a in ag if not a["hijacked"]][:5]
    for a in shown:
        L.append(f"| {a['agent_id']} | {a['agent_type']} | {a['score']:.2f} | {'yes' if a['hijacked'] else 'no'} |")
    L += ["", "A hijacked agent's score falls back once it has made 50 ordinary calls after the attack; the list shows each agent's "
          "score at the end of the run. `hijacked` is read from the sealed labels after scoring and covers every agent that "
          "ran an attack session.", ""]
    Path(path).write_text("\n".join(L), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4, 5])
    ap.add_argument("--sessions", type=int, default=1500)
    ap.add_argument("--out", default=str(RESULTS))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res, guard = run(a.seeds, a.sessions)
    (out / "toolguard.json").write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    write_md(res, guard, out / "toolguard.md")
    for name in ROWS:
        print(name, json.dumps(res["rows"][name]))
    for k, v in res["cases"].items():
        print(k, v)


if __name__ == "__main__":
    main()
