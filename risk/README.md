# Risk monitor

Collects every agent action as a [`shared/log_schema.json`](../shared/log_schema.json)
event, live over OpenTelemetry or offline from `.jsonl` logs, and scores each
agent across its sessions.

```
bank agents --OTLP/HTTP--> POST /v1/traces --+
anything ------JSON------> POST /events -----+--> validate (load_logs.validate_event) --> SQLite
.jsonl logs --> load_logs() ------------------------------------------------------------+
                                                                                        v
                                     Detector.score_agent(agent_id, {session_id: events})  (label stripped)
```

## Layout

| Path | What |
|---|---|
| `ingest/load_logs.py` | Offline: reads and validates `.jsonl` logs; `validate_event` is the one schema check, used live too |
| `ingest/otlp.py` | Decodes OTLP/HTTP exports (protobuf or JSON, gzip) |
| `ingest/events.py` | A span or a posted event -> a validated log event |
| `ingest/replay_otel.py` | Replays `.jsonl` logs as OTel spans; also the reference emitter for the agent team |
| `store.py` | SQLite of events; `pressure_mode` kept apart, never returned with events |
| `api/` | FastAPI app (`app.py`) and entry point (`server.py`) |
| `behaviorgpt/history.py` | A session -> BehaviorGPT history (`<agent_type>.<action>` items) |
| `scoring/detector.py` | The `Detector` interface, the same offline and online |
| `dashboard/`, `eval/` | Not started |

## Run

From the repo root, Python 3.11+:

```sh
python3 -m venv .venv && .venv/bin/pip install -r risk/requirements.txt
.venv/bin/uvicorn risk.api.server:app --port 4318 --reload
.venv/bin/python -m risk.ingest.replay_otel           # shared/sample_logs -> the monitor
curl -s localhost:4318/agents
.venv/bin/pytest risk
```

API docs: <http://localhost:4318/docs>. The database is `risk/data/monitor.sqlite` (`RISK_DB` to move it).

## For the agent team: emitting actions

The monitor reads exactly what the log schema says: trace = session, span = one action.
Port 4318 is the OTLP/HTTP default, so set `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`
(HTTP only, not gRPC). For each action, a span with:

- every log event field as an attribute, same names (`agent_id`, `agent_type`, `step`, `action`, `params`, `auth_level`, `policy_check`, `outcome`, `pressure_mode`, `reasoning_summary`)
- `params` as a JSON string, since OTel attributes can't nest
- `session_id` and `timestamp` optional: they default to the trace id and the span's start
- fields shared by every span of a process (`agent_id`, `agent_type`) may go on the resource instead

Spans without an `action` attribute (LLM calls, HTTP requests) are skipped. Actions
that fail validation are dropped and reported back to the exporter as rejected
spans (OTLP partial success), and logged by the server. See `emit_session` in
[`ingest/replay_otel.py`](ingest/replay_otel.py) for a working emitter.

Without OTel, `POST /events` takes one event or a list, exactly as a `.jsonl` line.

## API

| Endpoint | What it does |
|---|---|
| `POST /v1/traces` | OTLP/HTTP export, protobuf or JSON |
| `POST /events` | One event or a list; all or nothing, 422 names the first bad field |
| `GET /events?agent_id=&agent_type=&session_id=&action=&limit=&offset=` | Events, each session's together in step order |
| `GET /agents` | Per agent: sessions, events, skipped/failed policy checks, blocked, errors, elevated |
| `GET /agents/{id}` | One agent: its sessions (action sequences) and action counts |
| `GET /agents/{id}/history` | Its sessions as BehaviorGPT histories |
| `GET /agents/{id}/risk` | The detector's verdict (`no_detector` until one is plugged in) |
| `GET /sessions/{session_id}` | One session's events |
| `GET /eval/labels?agent_id=` | Ground truth `session_id -> pressure_mode`, for evaluation only |

Events are upserted by `agent_id:session_id:step`, so retries and re-sent logs are stored once.

## The label rule

`pressure_mode` is ground truth only. `load_logs` strips it, the store keeps it
in its own column, no events endpoint returns it, and detectors never see it.
Only `GET /eval/labels` exposes it, to measure a detector.

## Next: the detector

Implement `Detector.score_agent` in [`scoring/detector.py`](scoring/detector.py)
and pass it to `create_app(detector=...)` in [`api/server.py`](api/server.py).
Offline, feed it `sessions_by_agent(load_logs(folder)[0])`. The plan follows
Unbox's [behaviorgpt-anomaly](https://github.com/Unbox-AI/behaviorgpt-anomaly):
upload the action vocabulary as a catalog, score how likely each action was given
the session so far, accumulate surprise (CUSUM), and roll sessions up per agent.
