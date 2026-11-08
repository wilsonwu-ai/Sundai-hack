"""OpenTelemetry export. Traffic: one trace per client session, one SERVER span per request (OTLP/JSON).
Agent 10's own events go through the service agent's exporter, imported and not copied."""

import hashlib

from bank.agents.customer_service.otel import post_otlp, to_otlp_json  # noqa: F401  (re-exported)

from .traffic import parse_ts

SCOPE = "sundai.bank.api_gateway"
SERVER_KIND = 2
SKIP = {"session_id", "request_index", "ts"}


def _hex(seed, n):
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:n]
    return h if int(h, 16) else "1".rjust(n, "0")


def _value(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_value(x) for x in v]}}
    return {"stringValue": str(v)}


def _attrs(rec):
    return [{"key": k, "value": _value(v)} for k, v in rec.items() if v is not None]


def traffic_to_otlp_json(client_sessions):
    spans = []
    for reqs in client_sessions:
        if not reqs:
            continue
        sid = reqs[0]["session_id"]
        tid = _hex(f"trace/{sid}", 32)
        for r in reqs:
            start = int(round(parse_ts(r["ts"]).timestamp() * 1000)) * 1_000_000
            dur_ms = 4 + int(_hex(f"dur/{sid}/{r['request_index']}", 6), 16) % 280
            attrs = {k: v for k, v in r.items() if k not in SKIP}
            attrs["sundai.session_id"] = sid
            attrs["sundai.request_index"] = r["request_index"]
            code = r["http.response.status_code"]
            spans.append({
                "traceId": tid, "spanId": _hex(f"span/{sid}/{r['request_index']}", 16),
                "name": f"{r['http.request.method']} {r['http.route']}", "kind": SERVER_KIND,
                "startTimeUnixNano": str(start), "endTimeUnixNano": str(start + dur_ms * 1_000_000),
                "attributes": _attrs(attrs), "status": {"code": 2 if code >= 500 else 0},
            })
    return {"resourceSpans": [{
        "resource": {"attributes": _attrs({"service.name": "api.sundaibank.example"})},
        "scopeSpans": [{"scope": {"name": SCOPE, "version": "1"}, "spans": spans}],
    }]}
