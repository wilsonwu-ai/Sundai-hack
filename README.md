# Sundai Bank

Sundai Bank is a hackathon project in which eight AI banking agents (support, payments,
disputes, sales, collections, KYC, underwriting and ops) act on a mock bank, and every
action they take is logged as a structured event. A separate risk team uses those logs
to predict each agent's next action with BehaviorGPT and flags misalignment when an
agent's real behavior diverges from what an aligned agent would do.

## Repo layout and ownership

| Path      | Owner                                                         |
|-----------|---------------------------------------------------------------|
| `bank/`   | Agent team                                                    |
| `web/`    | Agent team                                                    |
| `risk/`   | Risk team                                                     |
| `shared/` | Both teams. **Announce in team chat before changing anything here.** |

Key shared contracts:

- [`shared/log_schema.json`](shared/log_schema.json): JSON Schema for one log event (one JSONL line = one agent action)
- [`shared/actions.md`](shared/actions.md): the fixed action vocabulary per agent, plus what misalignment looks like
- [`shared/sample_logs/`](shared/sample_logs/): hand-written example sessions (normal and pressure)

## Quick start (risk team)

```bash
python risk/ingest/load_logs.py                 # reads shared/sample_logs by default
python risk/ingest/load_logs.py path/to/logs    # or any folder of .jsonl files
```

The live monitor (OTLP in on port 4318, API docs at `/docs`); details in [`risk/README.md`](risk/README.md):

```bash
python3 -m venv .venv && .venv/bin/pip install -r risk/requirements.txt
.venv/bin/uvicorn risk.api.server:app --port 4318      # run from the repo root
.venv/bin/python -m risk.ingest.replay_otel            # replay shared/sample_logs into it as OTel spans
.venv/bin/pytest risk
```

## Workflow rules

1. **One branch per person**, prefixed by area: `bank/<name>-<topic>`, `web/<name>-<topic>` or `risk/<name>-<topic>`.
2. **Merge to `main` at least once an hour.** Small, frequent merges beat one giant merge at the end.
3. **`main` must always run.** Don't merge anything that breaks the build or the scripts above.
4. **Never commit `.env`.** Copy `.env.example` to `.env` and put your keys there; `.env` is gitignored.
