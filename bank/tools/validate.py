"""Validate events against shared/log_schema.json (JSON Schema) and the action vocabulary in shared/actions.md."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator

SHARED = Path(__file__).resolve().parents[2] / "shared"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class EventValidationError(ValueError):
    pass


@cache
def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads((SHARED / "log_schema.json").read_text(encoding="utf-8")))


@cache
def action_vocab() -> dict[str, frozenset[str]]:
    vocab, current = {}, None
    for line in (SHARED / "actions.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+(\w+)\s*$", line)
        if m:
            current = m.group(1)
        elif current and line.startswith("Actions:"):
            vocab[current] = frozenset(re.findall(r"`([a-z0-9_]+)`", line))
    return vocab


def validate_event(event: dict) -> None:
    errors = sorted(_validator().iter_errors(event), key=lambda e: list(e.path))
    if errors:
        e = errors[0]
        raise EventValidationError(f"{'/'.join(map(str, e.path)) or 'event'}: {e.message}")
    if event["step"] < 1 or isinstance(event["step"], bool):
        raise EventValidationError("step must be an integer >= 1")
    if not TS_RE.match(event["timestamp"]):
        raise EventValidationError(f"timestamp is not YYYY-MM-DDTHH:MM:SSZ: {event['timestamp']!r}")
    if event["action"] not in action_vocab().get(event["agent_type"], frozenset()):
        raise EventValidationError(f"action {event['action']!r} is not in shared/actions.md for {event['agent_type']}")
