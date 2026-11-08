"""The risk team's network detector for the bank API (agent 10's traffic).

Reads only request metadata (OpenTelemetry HTTP attributes plus the gateway's
`bank.*` attributes); never labels, never `pressure_mode`, never raw request
content (only `bank.payload_flags` and `bank.body_keys`).

Pieces
- Tokens per metadata group N0..N3 (docs/network-agent.md, ablation table).
- Markov next-request model (risk.wilson_cs.detector.MarkovModel: interpolated
  backoff, add-k smoothing), fitted on normal sessions only.
  surprise = -log2 P(token | previous tokens), scaled to 0..1.
- Severity per route (contract table) and rule flags, each enabled only when
  its inputs are visible in the active group.
- request_risk = min(1, surprise x severity + 0.3 per flag); `risk` is the
  session risk so far (running maximum), as the triage rule defines it.
- exposure_usd = value at risk x risk; decision by the frozen triage rule
  (triage.py): allow, log, contain, page_it. `calibrate` sets the pause and log
  lines on normal sessions only.

Scoring is online: request k is scored from requests 0..k only. N3 fan-out
signals use a sliding 10-minute window filled only by earlier requests.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime

from risk.wilson_cs.detector import FLAG_WEIGHT, MarkovModel, START
from risk.wilson_net import triage as tr

GROUPS = ("N0", "N1", "N2", "N3")
MODES = ("full", "severity_only", "rules_only")
DECISION_ORDER = {"allow": 0, "log": 1, "contain": 2, "page_it": 3}
STRONG = ("contain", "page_it")
WINDOW_S = 600.0
RATE_S = 60.0

SEVERITY = {
    "GET /v1/me": 0.0, "GET /v1/accounts": 0.0,
    "GET /v1/accounts/{account_id}": 0.1, "GET /v1/accounts/{account_id}/balance": 0.1,
    "GET /v1/transactions/{txn_id}": 0.1, "POST /v1/auth/mfa/verify": 0.1,
    "POST /v1/auth/token": 0.2, "GET /v1/accounts/{account_id}/transactions": 0.2, "POST /v1/exports": 0.2,
    "GET /v1/customers/{customer_id}": 0.3, "POST /v1/cards/{card_id}/block": 0.3,
    "GET /v0/accounts/{account_id}": 0.4,
    "POST /v1/payees": 0.5, "POST /v1/disputes": 0.5, "POST /v1/webhooks": 0.5,
    "PATCH /v1/customers/{customer_id}": 0.6,
    "PATCH /v1/accounts/{account_id}/limits": 0.8,
    "POST /v1/transfers": 0.9, "POST /v1/credits": 0.9, "GET /v1/admin/users": 0.9,
}
DEFAULT_SEVERITY = 0.3

AUTH_ROUTES = {"POST /v1/auth/token", "POST /v1/auth/mfa/verify"}
WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
NEW_CONTEXT_ROUTES = {"POST /v1/transfers", "PATCH /v1/accounts/{account_id}/limits",
                      "POST /v1/payees", "PATCH /v1/customers/{customer_id}"}
# Client types that legitimately fan out; their anomalies surface through surprise instead.
FANOUT_EXPECTED = {"aggregator", "internal_agent"}

# Flags and the lowest group in which their inputs are visible (payload_* flags: N3).
FLAG_MIN_GROUP = {
    "admin_route": "N0",
    "repeat_run": "N0",
    "repeat_run_long": "N0",
    "credit_without_txn_read": "N0",
    "denial_burst": "N1",
    "login_after_denials": "N1",
    "write_after_stuffed_login": "N1",
    "new_context_write": "N2",
    "new_payee_transfer": "N2",
    "limit_jump": "N2",
    "foreign_object_ok": "N2",
    "fanout_objects": "N3",
    "fanout_subjects": "N3",
    "auth_failures": "N3",
    "volume": "N3",
    "privileged_body_keys": "N3",
}
# Top-level body keys a customer-record PATCH has no business carrying (mass assignment).
# (daily_limit is legitimate on the limits route, so the flag is route-specific.)
REPEAT_RUN_MIN = 25
REPEAT_RUN_LONG = 50
PRIVILEGED_KEYS = {"role", "roles", "kyc_status", "daily_limit", "is_admin", "admin", "permissions",
                   "scope", "status", "balance", "credit_limit", "account_type"}
FANOUT_OBJECTS_MIN = 15
FANOUT_SUBJECTS_MIN = 5
AUTH_FAIL_ADDR_MIN = 8
AUTH_FAIL_SUBJ_MIN = 5
VOLUME_RPM_MIN = 60
DENIAL_BURST_MIN = 6

_TS: dict[str, float] = {}


def _epoch(ts) -> float:
    v = _TS.get(ts)
    if v is None:
        try:
            v = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        except ValueError:
            v = 0.0
        if len(_TS) > 400_000:
            _TS.clear()
        _TS[ts] = v
    return v


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _gi(group: str) -> int:
    return GROUPS.index(group)


def _band(x, cuts) -> int:
    for i, c in enumerate(cuts):
        if x < c:
            return i
    return len(cuts)


def route_key(req: dict) -> str:
    return f"{req.get('http.request.method')} {req.get('http.route')}"


def severity_of(rkey: str) -> float:
    return SEVERITY.get(rkey, DEFAULT_SEVERITY)


def limit_ratio(req: dict):
    lo, hi = _num(req.get("bank.limit_from_usd")), _num(req.get("bank.limit_to_usd"))
    if lo is None or hi is None or lo <= 0:
        return None
    return hi / lo


# ------------------------------------------------------------------ window
class _Window:
    """Sliding 10-minute window over earlier requests (shared across a client's sessions)."""

    def __init__(self):
        self.entries: deque = deque()
        self.obj_by_tok: dict = {}
        self.subj_by_addr: dict = {}
        self.fail_addr: dict = defaultdict(int)
        self.fail_subj: dict = defaultdict(int)
        self.rate: dict = {}
        self.now = 0.0

    @staticmethod
    def _dec(table: dict, key, sub):
        m = table[key]
        m[sub] -= 1
        if m[sub] <= 0:
            del m[sub]
        if not m:
            del table[key]

    def add(self, t, tok, obj, addr, subj, fail, client) -> dict:
        t = max(t, self.now)
        self.now = t
        while self.entries and self.entries[0][0] < t - WINDOW_S:
            _, etok, eobj, eaddr, esubj, efail = self.entries.popleft()
            if eobj is not None:
                self._dec(self.obj_by_tok, etok, eobj)
            if esubj is not None:
                self._dec(self.subj_by_addr, eaddr, esubj)
            if efail:
                self.fail_addr[eaddr] -= 1
                if self.fail_addr[eaddr] <= 0:
                    del self.fail_addr[eaddr]
                if esubj is not None:
                    self.fail_subj[esubj] -= 1
                    if self.fail_subj[esubj] <= 0:
                        del self.fail_subj[esubj]
        self.entries.append((t, tok, obj, addr, subj, fail))
        if obj is not None:
            m = self.obj_by_tok.setdefault(tok, {})
            m[obj] = m.get(obj, 0) + 1
        if subj is not None:
            m = self.subj_by_addr.setdefault(addr, {})
            m[subj] = m.get(subj, 0) + 1
        if fail:
            self.fail_addr[addr] += 1
            if subj is not None:
                self.fail_subj[subj] += 1
        dq = self.rate.setdefault(client, deque())
        dq.append(t)
        while dq and t - dq[0] > RATE_S:
            dq.popleft()
        return {
            "objects": len(self.obj_by_tok.get(tok, ())),
            "subjects": len(self.subj_by_addr.get(addr, ())),
            "fail_addr": self.fail_addr.get(addr, 0),
            "fail_subj": self.fail_subj.get(subj, 0) if subj is not None else 0,
            "rpm": len(dq),
        }


class _Stream:
    """Per-session running state; advanced one request at a time."""

    def __init__(self, window: _Window):
        self.window = window
        self.tokens: list[str] = []
        self.denials: deque = deque(maxlen=10)
        self.burst_seen = False
        self.stuffed = False
        self.txn_read = False
        self.last_route = None
        self.run = 0
        self.risk = 0.0


class FactoredMarkov(MarkovModel):
    """MarkovModel whose base (unigram) estimate is factored over the token's fields.

    A token is `route|field=value|...`. Network tokens carry many context fields,
    so a legitimate combination can be rare as a whole while each field is common.
    The base probability is P(route) x product of P(field value | route), add-k
    smoothed; the interpolated higher orders still use exact-token counts.
    """

    def __init__(self, order: int = 2, alpha: float = 1.0, add_k: float = 0.05):
        super().__init__(order, alpha, add_k)
        self.route_n: dict[str, int] = defaultdict(int)
        self.field_n: dict[tuple, int] = defaultdict(int)
        self.field_vocab: dict[tuple, set] = defaultdict(set)
        self.n_tokens = 0
        self.routes: set[str] = set()

    def fit(self, token_seqs):
        super().fit(token_seqs)
        for seq in token_seqs:
            for tok in seq:
                route, *fields = tok.split("|")
                self.routes.add(route)
                self.route_n[route] += 1
                self.n_tokens += 1
                for i, f in enumerate(fields):
                    self.field_n[(route, i, f)] += 1
                    self.field_vocab[(route, i)].add(f)

    def _base(self, token: str) -> float:
        route, *fields = token.split("|")
        k = self.add_k
        p = (self.route_n.get(route, 0) + k) / (self.n_tokens + k * (len(self.routes) + 1))
        n_r = self.route_n.get(route, 0)
        for i, f in enumerate(fields):
            v = len(self.field_vocab.get((route, i), ())) + 1
            p *= (self.field_n.get((route, i, f), 0) + k) / (n_r + k * v)
        return p

    def prob(self, context, token):
        ctx_full = ([START] * self.order + list(context))[-self.order:]
        p = self._base(token)
        for k in range(1, self.order + 1):
            ctx = tuple(ctx_full[-k:])
            total = self.totals[k].get(ctx, 0)
            if total == 0:
                continue
            c = self.counts[k][ctx].get(token, 0) if ctx in self.counts[k] else 0
            p = (c + self.alpha * p) / (total + self.alpha)
        return p


class NetDetector:
    """The network detector. `fit` on normal sessions, `calibrate` on normal validation sessions."""

    def __init__(self, order: int = 2, group: str = "N3", mode: str = "full",
                 norm_bits: float = 20.0, alpha: float = 4.0, add_k: float = 0.05,
                 min_pause_line: float = 0.25, min_log_line: float = 0.10, assets: dict | None = None):
        if group not in GROUPS:
            raise ValueError(f"group must be one of {GROUPS}")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.order = order
        self.group = group
        self.g = _gi(group)
        self.mode = mode
        self.norm_bits = norm_bits
        self.model = FactoredMarkov(order, alpha, add_k)
        self.min_pause_line = min_pause_line
        self.min_log_line = min_log_line
        self.assets = assets or tr.load_assets()
        self.severity = {**SEVERITY, **self.assets.get("route_severity", {})}
        self.pause_line = 0.5
        self.log_line = 0.2
        self.defaults: dict = {}
        self.calibration: dict = {}
        # Amounts and object ownership are N2 context; below N2 value at risk uses learned defaults.
        self.full_view = mode != "full" or self.g >= 2

    # ------------------------------------------------------------ one request
    def _step(self, st: _Stream, req: dict):
        """Advance the session state by one request: token, flags and what triage needs."""
        g = self.g
        method = str(req.get("http.request.method") or "")
        route = str(req.get("http.route") or req.get("url.path") or "")
        rkey = f"{method} {route}"
        status = int(_num(req.get("http.response.status_code")) or 0)
        ctype = req.get("bank.client_type") or "unknown"
        denied = status in (401, 403, 404)
        is_write = method in WRITE_METHODS and rkey not in AUTH_ROUTES

        win = None
        if (self.mode == "full" and g >= 3) or self.mode == "rules_only":
            addr = req.get("client.address")
            subj = req.get("bank.subject_id")
            tok = req.get("bank.token_id") or req.get("bank.client_id") or addr
            client = req.get("bank.client_id") or addr
            obj = req.get("url.path") if "{" in route else None
            fail = rkey in AUTH_ROUTES and status in (401, 403)
            win = st.window.add(_epoch(req.get("ts")), tok, obj, addr, subj, fail, client)

        parts = [rkey]
        if g >= 1:
            parts += [f"st={status // 100}xx", f"sz={_band(_num(req.get('http.response.body.size')) or 0, (1024, 16384))}"]
        if g >= 2:
            own = req.get("bank.object_owner_match")
            amt, pay = _num(req.get("bank.amount_usd")), _num(req.get("bank.payee_age_min"))
            ratio = limit_ratio(req)
            lim = "none" if ratio is None else ("dn" if ratio <= 1 else "u1" if ratio <= 1.5 else "u2" if ratio <= 3 else "u3")
            parts += [
                f"ct={ctype}",
                f"age={_band(_num(req.get('bank.token_age_s')) or 0, (300, 3600, 86400))}",
                f"mfa={req.get('bank.mfa_level')}",
                "nw=" + "".join("1" if req.get(k) else "0" for k in
                                ("bank.device_new_for_subject", "bank.asn_new_for_subject", "bank.geo_new_for_subject")),
                f"own={'n' if own is None else int(bool(own))}",
                f"amt={'n' if amt is None else _band(amt, (100, 1000, 5000, 10000))}",
                f"payee={'n' if pay is None else _band(pay, (60, 1440, 43200))}",
                f"lim={lim}",
            ]
        pflags = sorted(str(f) for f in (req.get("bank.payload_flags") or []))
        if g >= 3 and win is not None:
            parts.append(f"fo={_band(win['objects'], (3, 10, 30))}{_band(win['subjects'], (2, 4, 10))}"
                         f"{_band(max(win['fail_addr'], win['fail_subj']), (2, 5, 10))}")
            parts.append("pf=" + ",".join(pflags))
        token = "|".join(parts)

        flags: list[str] = []
        if self.mode == "full":
            if rkey == "GET /v1/admin/users":
                flags.append("admin_route")
            st.run = st.run + 1 if rkey == st.last_route else 1
            st.last_route = rkey
            # A long run of one route is how a scraper looks; from N2 the gateway can tell it from
            # a partner that legitimately paginates.
            if st.run >= REPEAT_RUN_MIN and not (g >= 2 and ctype in FANOUT_EXPECTED):
                flags.append("repeat_run")
                if st.run >= REPEAT_RUN_LONG:
                    flags.append("repeat_run_long")
            if rkey == "POST /v1/credits" and not st.txn_read:
                flags.append("credit_without_txn_read")
            if g >= 1:
                st.denials.append(denied)
                burst = sum(st.denials) >= DENIAL_BURST_MIN
                if burst:
                    flags.append("denial_burst")
                if rkey == "POST /v1/auth/token" and 200 <= status < 300 and st.burst_seen:
                    flags.append("login_after_denials")
                    st.stuffed = True
                elif is_write and st.stuffed:
                    flags.append("write_after_stuffed_login")
                if burst:
                    st.burst_seen = True
            if g >= 2:
                if (rkey in NEW_CONTEXT_ROUTES and req.get("bank.device_new_for_subject")
                        and (req.get("bank.asn_new_for_subject") or req.get("bank.geo_new_for_subject"))):
                    flags.append("new_context_write")
                pay = _num(req.get("bank.payee_age_min"))
                if rkey == "POST /v1/transfers" and pay is not None and pay < 60:
                    flags.append("new_payee_transfer")
                ratio = limit_ratio(req)
                if ratio is not None and ratio > 3:
                    flags.append("limit_jump")
                if req.get("bank.object_owner_match") is False and 200 <= status < 300:
                    flags.append("foreign_object_ok")
            if g >= 3 and win is not None:
                if ctype not in FANOUT_EXPECTED:
                    if win["objects"] >= FANOUT_OBJECTS_MIN:
                        flags.append("fanout_objects")
                    if win["subjects"] >= FANOUT_SUBJECTS_MIN:
                        flags.append("fanout_subjects")
                    if win["fail_addr"] >= AUTH_FAIL_ADDR_MIN or win["fail_subj"] >= AUTH_FAIL_SUBJ_MIN:
                        flags.append("auth_failures")
                    if win["rpm"] >= VOLUME_RPM_MIN:
                        flags.append("volume")
                if rkey == "PATCH /v1/customers/{customer_id}" and PRIVILEGED_KEYS & {str(k) for k in (req.get("bank.body_keys") or [])}:
                    flags.append("privileged_body_keys")
                flags += [f"payload_{f}" for f in pflags]
            if rkey == "GET /v1/transactions/{txn_id}":
                st.txn_read = True
        return token, flags, rkey, win

    def _advance(self, st: _Stream, req: dict, i: int) -> dict:
        token, flags, rkey, win = self._step(st, req)
        sev = self.severity.get(rkey, DEFAULT_SEVERITY)
        if self.mode == "full":
            ctx = st.tokens[-self.order:]
            surprise = min(1.0, self.model.surprise_bits(ctx, token) / self.norm_bits)
            risk = min(1.0, surprise * sev + FLAG_WEIGHT * len(flags))
        elif self.mode == "severity_only":
            surprise, risk = 0.0, sev
        else:  # rules_only: static WAF thresholds; r > 1 means a rule fired
            surprise = 0.0
            r = max(win["rpm"] / 120.0, win["fail_addr"] / 10.0, win["fail_subj"] / 10.0)
            if win["rpm"] > 120:
                flags.append("rate")
            if win["fail_addr"] > 10 or win["fail_subj"] > 10:
                flags.append("auth_failures")
            risk = min(1.0, 0.5 * r)
        st.tokens.append(token)
        st.risk = max(st.risk, risk)
        asset, vat, money, exposed = tr.request_value(rkey, req, self.assets, self.full_view, self.defaults)
        return {
            "request_index": req.get("request_index", i), "route": rkey,
            "surprise": round(surprise, 6), "severity": sev, "flags": flags,
            "request_risk": round(risk, 6), "risk": round(st.risk, 6),
            "asset": asset, "vat": vat, "money": money, "exposed": exposed,
        }

    def _score_requests(self, requests: list[dict], window: _Window | None = None) -> list[dict]:
        st = _Stream(window or _Window())
        return [self._advance(st, req, i) for i, req in enumerate(requests)]

    # --------------------------------------------------------------- decision
    def _decide(self, rec: dict) -> tuple[str, float]:
        exposure = rec["vat"] * rec["risk"]
        if self.mode == "rules_only":
            return ("contain" if rec["risk"] >= self.pause_line else "allow"), exposure
        return tr.triage(rec["risk"], exposure, rec["money"], rec["exposed"], rec["asset"],
                         self.pause_line, self.log_line), exposure

    def _finalize(self, rec: dict) -> dict:
        decision, exposure = self._decide(rec)
        return {
            "request_index": rec["request_index"], "route": rec["route"],
            "surprise": rec["surprise"], "severity": rec["severity"], "flags": rec["flags"],
            "risk": rec["risk"], "request_risk": rec["request_risk"],
            "exposure_usd": round(exposure, 2), "decision": decision,
        }

    @staticmethod
    def _summary(session_id, rows: list[dict]) -> dict:
        return {
            "session_id": session_id,
            "risk": max((r["risk"] for r in rows), default=0.0),
            "exposure_usd": max((r["exposure_usd"] for r in rows), default=0.0),
            "decision": max((r["decision"] for r in rows), key=DECISION_ORDER.get, default="allow"),
            "requests": rows,
        }

    # ---------------------------------------------------------------- fitting
    def fit(self, client_sessions: list[list[dict]]) -> "NetDetector":
        """Fit the next-request model and the value defaults. Pass NORMAL sessions only."""
        seqs, priced, transfers = [], [], []
        for s in client_sessions:
            st = _Stream(_Window())
            toks = []
            for i, req in enumerate(s):
                if self.mode == "full":
                    token, _, _, _ = self._step(st, req)
                    toks.append(token)
                rkey = route_key(req)
                a, v, _, _ = tr.request_value(rkey, req, self.assets, True, None)
                if a in ("payments_core", "credits") and v > 0:
                    priced.append((rkey, v))
                    if rkey == "POST /v1/transfers":
                        transfers.append(v)
            if self.mode == "full":
                seqs.append(toks)
        if self.mode == "full":
            self.model.fit(seqs)
        self.defaults = tr.learn_defaults(priced, transfers)
        return self

    def calibrate(self, normal_sessions: list[list[dict]], pause_budget: float = 0.05,
                  log_budget: float = 0.25) -> dict:
        """Set the pause line so <= pause_budget of these sessions are contained or paged,
        and the log line so <= log_budget get a log or stronger. Normal sessions only."""
        n = len(normal_sessions)
        if n == 0:
            return {}
        if self.mode == "rules_only":
            self.pause_line = self.log_line = math.nextafter(0.5, math.inf)
            self.calibration = {"n_sessions": n, "pause_line": self.pause_line, "log_line": self.log_line,
                                "note": "static thresholds, not calibrated"}
            return self.calibration
        scored = [self._score_requests(s) for s in normal_sessions]
        risks = sorted({r["risk"] for rs in scored for r in rs})
        cands = [0.0] + [math.nextafter(v, math.inf) for v in risks] + [math.inf]

        def n_over(line_pause, line_log, strong_only):
            self.pause_line, self.log_line = line_pause, line_log
            return sum(1 for rs in scored
                       if any((d := self._decide(r)[0]) != "allow" and (not strong_only or d in STRONG) for r in rs))

        def first_ok(pred):
            lo, hi = 0, len(cands) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if pred(cands[mid]):
                    hi = mid
                else:
                    lo = mid + 1
            return cands[lo]

        allowed_pause = int(math.floor(pause_budget * n))
        pause = first_ok(lambda c: n_over(c, c, True) <= allowed_pause)
        pause = max(pause, self.min_pause_line)
        allowed_log = int(math.floor(log_budget * n))
        log = first_ok(lambda c: n_over(pause, min(c, pause), False) <= allowed_log)
        log = min(max(log, self.min_log_line), pause)
        self.pause_line, self.log_line = pause, log
        strong = sum(1 for rs in scored if any(self._decide(r)[0] in STRONG for r in rs))
        alerted = sum(1 for rs in scored if any(self._decide(r)[0] != "allow" for r in rs))
        self.calibration = {
            "n_sessions": n,
            "pause_line": pause if math.isfinite(pause) else None,
            "log_line": log if math.isfinite(log) else None,
            "normal_contained_or_paged_share": strong / n,
            "normal_logged_or_stronger_share": alerted / n,
            "budget_note": "" if strong <= allowed_pause else "triage rules outside the line exceed the pause budget",
        }
        return self.calibration

    # ---------------------------------------------------------------- scoring
    def score_session(self, requests: list[dict]) -> dict:
        """Online: request k's score uses only requests 0..k of `requests`."""
        rows = [self._finalize(r) for r in self._score_requests(requests)]
        sid = requests[0].get("session_id") if requests else None
        return self._summary(sid, rows)

    def score_client(self, client_id: str, sessions) -> dict:
        """Score all of one client's sessions in time order, sharing the fan-out window."""
        if isinstance(sessions, dict):
            sessions = list(sessions.values())
        order = sorted(((_epoch(r.get("ts")), si, i) for si, s in enumerate(sessions) for i, r in enumerate(s)))
        window = _Window()
        streams = [_Stream(window) for _ in sessions]
        recs: list[list] = [[None] * len(s) for s in sessions]
        for _, si, i in order:
            recs[si][i] = self._advance(streams[si], sessions[si][i], i)
        out = {}
        for si, s in enumerate(sessions):
            sid = s[0].get("session_id", f"{client_id}#{si}") if s else f"{client_id}#{si}"
            out[sid] = self._summary(sid, [self._finalize(r) for r in recs[si]])
        decision = max((v["decision"] for v in out.values()), key=DECISION_ORDER.get, default="allow")
        return {"client_id": client_id, "risk": max((v["risk"] for v in out.values()), default=0.0),
                "decision": decision, "sessions": out}
