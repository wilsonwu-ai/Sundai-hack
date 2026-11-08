"""Log events from the live sources: OTel spans and `POST /events`.

Both end up as shared/log_schema.json events, checked by the same
`validate_event` the offline loader uses.

The agents emit one span per action, with the log event's fields as span
attributes (trace = session). A field may also sit on the resource, e.g.
`agent_id` and `agent_type` for a process that runs one agent. `params` is an
object in the schema but OTel attributes can't nest, so it travels as a JSON
string. Spans without an `action` attribute (LLM calls, HTTP requests, a
session's root span) are not actions and are skipped.
"""

import json
from datetime import UTC, datetime

from risk.ingest.load_logs import LogValidationError, load_schema, validate_event

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"  # fixed width, so stored timestamps sort as text


def from_span(span: dict) -> dict | None:
    """The log event a span carries, or None if the span is not an action."""
    if "action" not in span["attributes"]:
        return None
    _, properties = load_schema()
    attrs = {**span["resource"], **span["attributes"]}
    event = {k: attrs[k] for k in properties if k in attrs}
    event.setdefault("session_id", span["trace_id"])
    if "timestamp" not in event and span["start_ns"]:
        event["timestamp"] = datetime.fromtimestamp(span["start_ns"] / 1e9, tz=UTC).strftime(TS_FORMAT)
    if isinstance(event.get("params"), str):
        try:
            event["params"] = json.loads(event["params"])
        except ValueError:
            pass  # left a string: validation rejects it, naming the field
    return event


def check(event: dict, where: str) -> dict:
    """The event validated, with its timestamp normalized to UTC."""
    validate_event(event, where)
    try:
        ts = datetime.fromisoformat(event["timestamp"])
    except ValueError:
        raise LogValidationError(
            f"{where}: 'timestamp' is not ISO 8601: {event['timestamp']!r}"
        ) from None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return {**event, "timestamp": ts.astimezone(UTC).strftime(TS_FORMAT)}
