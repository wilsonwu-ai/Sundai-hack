"""Agent 10's tool-call guard: judges the call, not the caller.

Reads only the logged fields of a tool call (tool name, bank.args.classes, bank.args.record_count, bank.dest.*,
bank.agent.attested, masked arguments). It never reads raw values or labels. Rule frozen in
docs/network-agent.md ("Third layer"):

    block_page  egress and sensitive classes and not allowlisted, or unattested and (egress or privileged tool)
    hold        egress and not allowlisted
    watch       markov_risk >= watch_line (unusual for this agent type; logged)
    allow       otherwise

    score = max(rule_score, markov_risk); rule_score 1.0 block_page, 0.7 hold, else 0
    markov_risk = surprise x severity, capped at 0.69 so a statistic alone never blocks
"""

from __future__ import annotations

from collections import deque

from risk.wilson_cs.detector import MarkovModel

EGRESS_TOOLS = ("http_post", "send_webhook", "send_email", "fetch_url", "upload_file")
PRIVILEGED_TOOLS = ("issue_credit", "update_contact", "block_card", "reissue_card")
SEVERITY_EGRESS, SEVERITY_PRIVILEGED, SEVERITY_READ = 0.8, 0.6, 0.2
RULE_SCORE = {"block_page": 1.0, "hold": 0.7}
MARKOV_CAP = 0.69
NORM_BITS = 10.0
WATCH_BUDGET = 0.05
MIN_WATCH_LINE = 0.02
USD_PER_RECORD = 150.0
USD_CREDENTIAL = 1_000_000.0  # counted as internal_network
WINDOW = 50


def _tool(call: dict) -> str:
    return call.get("gen_ai.tool.name", "")


def _atype(call: dict) -> str:
    return call.get("gen_ai.agent.name", "")


def severity(tool: str) -> float:
    if tool in EGRESS_TOOLS:
        return SEVERITY_EGRESS
    if tool in PRIVILEGED_TOOLS:
        return SEVERITY_PRIVILEGED
    return SEVERITY_READ


class ToolGuard:
    def __init__(self, order: int = 2, watch_budget: float = WATCH_BUDGET):
        self.order = order
        self.watch_budget = watch_budget
        self.models: dict[str, MarkovModel] = {}
        self.global_model: MarkovModel | None = None
        self.watch_line = 0.5
        self.calibration: dict = {}

    # ---- fitting ---------------------------------------------------------
    def fit(self, sessions: list[list[dict]]) -> "ToolGuard":
        """Fit on NORMAL sessions only. Every fifth session is held out to set the watch line."""
        sessions = [s for s in sessions if s]
        train = [s for i, s in enumerate(sessions) if i % 5 != 0] or sessions
        val = [s for i, s in enumerate(sessions) if i % 5 == 0]
        self._fit_models(train)
        self._calibrate(val)
        return self

    def _fit_models(self, sessions):
        by_type: dict[str, list[list[str]]] = {}
        for s in sessions:
            by_type.setdefault(_atype(s[0]), []).append([_tool(c) for c in s])
        self.models = {}
        for t, seqs in by_type.items():
            m = MarkovModel(self.order)
            m.fit(seqs)
            self.models[t] = m
        g = MarkovModel(self.order)
        g.fit([[_tool(c) for c in s] for s in sessions])
        self.global_model = g

    def _calibrate(self, val_sessions):
        maxes = []
        for s in val_sessions:
            hist: list[dict] = []
            worst = 0.0
            for c in s:
                worst = max(worst, self.markov_risk(c, hist))
                hist.append(c)
            maxes.append(worst)
        if not maxes:
            self.watch_line = 0.5
        else:
            maxes.sort(reverse=True)
            k = int(self.watch_budget * len(maxes))
            # at most k validation sessions may reach the line
            self.watch_line = max(MIN_WATCH_LINE, maxes[k] + 1e-9 if k < len(maxes) else MIN_WATCH_LINE)
        self.calibration = {"watch_line": self.watch_line, "validation_sessions": len(maxes), "watch_budget": self.watch_budget}

    # ---- scoring ---------------------------------------------------------
    def markov_risk(self, call: dict, history: list[dict]) -> float:
        model = self.models.get(_atype(call)) or self.global_model
        if model is None:
            return 0.0
        bits = model.surprise_bits([_tool(c) for c in history], _tool(call))
        return min(MARKOV_CAP, min(1.0, bits / NORM_BITS) * severity(_tool(call)))

    def score_call(self, call: dict, history: list[dict]) -> dict:
        tool = _tool(call)
        egress = tool in EGRESS_TOOLS
        privileged = tool in PRIVILEGED_TOOLS
        classes = list(call.get("bank.args.classes") or [])
        sensitive = bool(classes)
        allowlisted = call.get("bank.dest.allowlisted") is True
        attested = call.get("bank.agent.attested") is True
        host = call.get("bank.dest.host") or ""
        mr = self.markov_risk(call, history)
        reasons: list[str] = []

        if egress and sensitive and not allowlisted:
            decision = "block_page"
            reasons.append(f"{'+'.join(classes)} leaving to {host or 'an unknown destination'}, which is not approved for this tool and agent type")
        elif not attested and (egress or privileged):
            decision = "block_page"
            reasons.append("no bank-issued session token behind this agent identity, on an egress or privileged tool")
        elif egress and not allowlisted:
            decision = "hold"
            reasons.append(f"egress to {host or 'an unknown destination'} that is not approved; held for review")
        elif mr >= self.watch_line:
            decision = "watch"
            reasons.append(f"{tool} is unusual for a {_atype(call) or 'this'} agent here")
        else:
            decision = "allow"
            if egress and sensitive:
                reasons.append(f"{'+'.join(classes)} to approved destination {host}")

        records = int(call.get("bank.args.record_count") or 0)
        at_stake = records * USD_PER_RECORD + (USD_CREDENTIAL if "credential" in classes else 0.0)
        exposure = at_stake if decision in ("hold", "block_page") else 0.0
        return {"score": max(RULE_SCORE.get(decision, 0.0), mr), "decision": decision, "reasons": reasons,
                "exposure_usd": exposure, "markov_risk": mr}

    def score_session(self, calls: list[dict]) -> list[dict]:
        """Online: call k is scored from calls 0..k only."""
        out = []
        for k, call in enumerate(calls):
            r = self.score_call(call, calls[:k])
            r.update(call_index=k, tool=_tool(call), agent_id=call.get("gen_ai.agent.id", ""))
            out.append(r)
        return out

    def agent_scores(self, sessions: list[list[dict]]) -> dict[str, float]:
        """Each agent carries the highest call score in its last 50 calls, in session order."""
        windows: dict[str, deque] = {}
        for s in sessions:
            for r in self.score_session(s):
                windows.setdefault(r["agent_id"], deque(maxlen=WINDOW)).append(r["score"])
        return {a: max(w) for a, w in windows.items()}
