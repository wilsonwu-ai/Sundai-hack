"""OpenTelemetry export: one trace per session, one span per action (OTLP/JSON, ExportTraceServiceRequest)."""

import hashlib
import json
import urllib.request
from datetime import datetime

SCOPE = "sundai.bank.service_agent"
EVENT_FIELDS = ("agent_id", "agent_type", "session_id", "step", "timestamp", "action", "params",
                "auth_level", "policy_check", "outcome", "pressure_mode", "reasoning_summary")


def _hex(seed, n):
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:n]
    return h if int(h, 16) else "1".rjust(n, "0")  # an all-zero id is invalid in OTLP


def trace_id(session_id):
    return _hex(f"trace/{session_id}", 32)


def span_id(session_id, step):
    return _hex(f"span/{session_id}/{step}", 16)


def _value(v):
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def _attrs(d):
    return [{"key": k, "value": _value(v)} for k, v in d.items()]


def _ns(ts):
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()) * 1_000_000_000


def to_otlp_json(sessions):
    spans = []
    for events in sessions:
        if not events:
            continue
        sid = events[0]["session_id"]
        tid = trace_id(sid)
        root = _hex(f"root/{sid}", 16)
        starts = [_ns(e["timestamp"]) for e in events]
        ends = [s + (300 + (e["step"] * 137) % 900) * 1_000_000 for s, e in zip(starts, events)]
        spans.append({
            "traceId": tid, "spanId": root, "name": f"session {sid}", "kind": 1,
            "startTimeUnixNano": str(starts[0]), "endTimeUnixNano": str(max(ends)),
            "attributes": _attrs({"gen_ai.conversation.id": sid, "gen_ai.operation.name": "invoke_agent",
                                  "session.steps": len(events)}),
            "status": {"code": 1},
        })
        for e, s, en in zip(events, starts, ends):
            attrs = {k: (json.dumps(e[k], sort_keys=True) if k == "params" else e[k]) for k in EVENT_FIELDS}
            attrs["gen_ai.operation.name"] = "execute_tool"
            attrs["gen_ai.tool.name"] = e["action"]
            attrs["gen_ai.conversation.id"] = sid
            spans.append({
                "traceId": tid, "spanId": span_id(sid, e["step"]), "parentSpanId": root,
                "name": f"execute_tool {e['action']}", "kind": 1,
                "startTimeUnixNano": str(s), "endTimeUnixNano": str(en),
                "attributes": _attrs(attrs),
                "status": {"code": 2 if e["outcome"] in ("blocked", "error") else 1},
            })
    return {"resourceSpans": [{
        "resource": {"attributes": _attrs({"service.name": "sundai-bank-service-agent"})},
        "scopeSpans": [{"scope": {"name": SCOPE, "version": "1"}, "spans": spans}],
    }]}


def post_otlp(body, endpoint="http://localhost:4318/v1/traces"):
    """POST the OTLP/JSON body and return the HTTP status code. Never called in tests."""
    req = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status
