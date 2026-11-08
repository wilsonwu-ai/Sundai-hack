"""Load Sundai Bank agent logs into per-session action sequences.

Dependency-free (standard library only).

Usage:
    python risk/ingest/load_logs.py                  # defaults to shared/sample_logs
    python risk/ingest/load_logs.py path/to/folder   # any folder of .jsonl files

From code:
    from load_logs import load_logs
    sequences, labels = load_logs("shared/sample_logs")

`sequences` is a list of sessions; each session is a list of events sorted by
`step`, with `pressure_mode` REMOVED from every event. `labels` maps
session_id -> pressure_mode (True = pressure, False = normal).

pressure_mode is a ground-truth label only. It is stripped from the sequences
so that it can never leak into the risk model's inputs.
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "shared" / "log_schema.json"
ACTIONS_PATH = REPO_ROOT / "shared" / "actions.md"
DEFAULT_LOG_DIR = REPO_ROOT / "shared" / "sample_logs"

LABEL_FIELD = "pressure_mode"

# JSON Schema type name -> Python type(s). bool is excluded from "integer" below.
_JSON_TYPES = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "object": dict,
}


class LogValidationError(ValueError):
    """Raised when a log line does not match shared/log_schema.json."""


def _load_schema():
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    return schema["required"], schema["properties"]


def _load_action_vocab():
    """Parse shared/actions.md into {agent_type: {action, ...}}."""
    vocab = {}
    current = None
    for line in ACTIONS_PATH.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^##\s+(\w+)\s*$", line)
        if heading:
            current = heading.group(1)
            continue
        if current and line.startswith("Actions:"):
            vocab[current] = set(re.findall(r"`([a-z0-9_]+)`", line))
    return vocab


def _validate_event(event, where, required, properties, vocab):
    if not isinstance(event, dict):
        raise LogValidationError(f"{where}: expected a JSON object, got {type(event).__name__}")

    missing = [field for field in required if field not in event]
    if missing:
        raise LogValidationError(f"{where}: missing required field(s): {', '.join(missing)}")

    unknown = sorted(set(event) - set(properties))
    if unknown:
        raise LogValidationError(f"{where}: unknown field(s) not in schema: {', '.join(unknown)}")

    for field, spec in properties.items():
        value = event[field]
        expected = spec.get("type")
        py_type = _JSON_TYPES.get(expected)
        wrong_type = py_type is not None and not isinstance(value, py_type)
        if expected == "integer" and isinstance(value, bool):
            wrong_type = True
        if wrong_type:
            raise LogValidationError(
                f"{where}: field '{field}' should be {expected}, got {type(value).__name__} ({value!r})"
            )
        if "enum" in spec and value not in spec["enum"]:
            raise LogValidationError(
                f"{where}: field '{field}' has invalid value {value!r}; allowed: {', '.join(spec['enum'])}"
            )

    if event["step"] < 1:
        raise LogValidationError(f"{where}: 'step' must be >= 1, got {event['step']}")

    allowed_actions = vocab.get(event["agent_type"], set())
    if event["action"] not in allowed_actions:
        raise LogValidationError(
            f"{where}: action {event['action']!r} is not in shared/actions.md for "
            f"agent_type {event['agent_type']!r}; allowed: {', '.join(sorted(allowed_actions))}"
        )


def load_logs(folder=DEFAULT_LOG_DIR):
    """Read every .jsonl file in `folder` and return (sequences, labels)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Log folder not found: {folder}")

    files = sorted(folder.glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"No .jsonl files found in {folder}")

    required, properties = _load_schema()
    vocab = _load_action_vocab()

    by_session = defaultdict(list)
    for path in files:
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                where = f"{path.name}:{line_no}"
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as e:
                    raise LogValidationError(f"{where}: invalid JSON ({e.msg})") from e
                _validate_event(event, where, required, properties, vocab)
                by_session[event["session_id"]].append(event)

    sequences = []
    labels = {}
    for session_id in sorted(by_session):
        events = sorted(by_session[session_id], key=lambda e: e["step"])

        steps = [e["step"] for e in events]
        if len(steps) != len(set(steps)):
            raise LogValidationError(f"session {session_id}: duplicate step numbers {steps}")

        session_labels = {e[LABEL_FIELD] for e in events}
        if len(session_labels) != 1:
            raise LogValidationError(f"session {session_id}: mixed {LABEL_FIELD} values within one session")

        labels[session_id] = session_labels.pop()
        sequences.append([{k: v for k, v in e.items() if k != LABEL_FIELD} for e in events])

    return sequences, labels


def main(argv):
    folder = Path(argv[1]) if len(argv) > 1 else DEFAULT_LOG_DIR
    try:
        sequences, labels = load_logs(folder)
    except (LogValidationError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    for session in sequences:
        first = session[0]
        actions = " -> ".join(e["action"] for e in session)
        label = "pressure" if labels[first["session_id"]] else "normal"
        print(f"{first['agent_id']} | {first['session_id']} | {actions} | label={label}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
