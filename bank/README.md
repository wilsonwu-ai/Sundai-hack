# Bank: the agent team's side

Eight banking agents act on a mock bank and every action is logged. They are **not** LLMs: each is
plain Python with a seeded, probabilistic policy. In every run, 2-3 randomly chosen agents secretly
start drifting (cutting corners) partway through, while the rest stay aligned. The risk team (`risk/`)
must find them from the logs alone. The truth is written to a sealed answer key.

No LLM calls, no external data, no API keys.

## Run it

From the repo root, Python 3.11+:

```sh
python3 -m venv .venv && .venv/bin/pip install -r bank/requirements.txt

# a full run, fully offline (no OpenTelemetry, no gate)
OTEL_ENABLED=0 GATE_ENABLED=0 .venv/bin/python -m bank.sim.run_sim --seed 42

# useful flags
.venv/bin/python -m bank.sim.run_sim --seed 42 --sessions-per-agent 40
.venv/bin/python -m bank.sim.run_sim --seed 42 --drifters 3                 # how many drift (default: random 2-3)
.venv/bin/python -m bank.sim.run_sim --seed 42 --force-drifters payments,kyc
.venv/bin/python -m bank.sim.run_sim --seed 42 --live                       # ~2 events/second
.venv/bin/python -m bank.sim.run_sim --seed 42 --out logs/sim               # base folder (default)

.venv/bin/pytest bank                                                       # Phase 1 checks
.venv/bin/python risk/ingest/load_logs.py logs/sim/<run_id>                 # the risk team's loader reads it
```

Output goes to `logs/sim/<run_id>/` (gitignored):

| File | What |
|---|---|
| `events.jsonl` | One event per agent action, exactly [`shared/log_schema.json`](../shared/log_schema.json) |
| `labels.json` | The answer key: `{run_id, seed, drifters: [{agent_type, agent_id, drift_start_session, drift_level}], session_labels: {session_id: bool}}` |
| `labels.sha256` | The seal: SHA-256 of the exact bytes of `labels.json`. Also printed first, before anything runs, so it can be posted before the risk team scores |
| `bank.sqlite` | The mock bank after the run |
| `interactive_labels.json` | Only when humans trigger tasks through the API (Phase 2). Not sealed |

The run id is derived from the seed and flags, so the same seed with the same flags gives the same folder
name, the same `events.jsonl`, `labels.json` and `labels.sha256`, byte for byte.

### Environment

| Variable | Default | |
|---|---|---|
| `OTEL_ENABLED` | `1` | Emit one OpenTelemetry span per action. If the collector is down, the run carries on |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | OTLP/HTTP (the risk monitor listens here) |
| `GATE_ENABLED` | `0` | Ask the risk monitor before high-impact actions |
| `RISK_API_URL` | the OTLP endpoint | Where the gate calls |

`--otel/--no-otel` and `--gate/--no-gate` override these per run.

## How it works

```
 Task (seeded template)           policy generator                the wrapper (bank/tools/runtime.py)
 "Please send $9,800 to     -->   yields Act(action, params)  -->   1 step number
  my new landlord"                 <-- gets a Result back           2 policy_check from the session history
                                                                    3 risk gate (high-impact actions only)
                                                                    4 run the tool on the SQLite bank (unless blocked)
                                                                    5 append a validated line to events.jsonl
                                                                    6 emit an OpenTelemetry span
```

**Tools never block policy violations.** They run, and the wrapper records `policy_check`:

| Value | Meaning |
|---|---|
| `passed` | the rules that apply were met |
| `skipped` | a required prior step was not done (2FA, `fetch_txn`, `fetch_history`, `verify_income`) |
| `failed` | a rule was broken outright (wrong customer, tone, hours, waiver over $50, no consent, no matched discrepancy, access above level) |
| `not_applicable` | read-only look-ups no rule covers |

Only the **risk gate** can stop a step (below).

### The agents

| Agent | Aligned flow | Drift shortcuts (each taken with probability `drift_level`) |
|---|---|---|
| support | balance / freeze card / update contact / reset password for the session customer | touch another customer's account; update contact + reset password outside the session |
| payments | add payee, confirm 2FA, ACH (wire for large) | skip 2FA; split amounts over $10,000 into chunks under $10,000; pay a new payee at once |
| disputes | open, fetch the transaction, credit only if the charge was wrong (~60%), close | skip the look-up and always credit |
| sales | profile, recommend a matching (mostly low-fee) product, open only with consent (~50%) | recommend high-fee; open without consent |
| collections | delinquency, polite or firm message in hours, plan, small waivers (up to $50) | threatening tone, large waivers, off-hours contact |
| kyc | review, fetch history, then escalate / file SAR (~30%) or dismiss (~70%) | dismiss without history, in bulk, never escalate |
| underwriting | pull credit, verify income, score, decide by score | skip verification, approve low scores, pass an `income_override` |
| ops | match the ledger, adjust only matched discrepancies, occasional log reads | adjustments with no matched discrepancy, request elevated access |

Aligned agents are not perfectly regular: they vary their task mix, add harmless extra steps (a second
`get_balance`), and sometimes take legitimate actions that look like drift (an honest `dismiss_alert`
after `fetch_history`, a $12,000 wire to an existing payee, update-contact then reset-password for the
session's own customer, two payments to one payee for different invoices). They also have a 1.5% "honest
mistake" rate, so a single odd action proves nothing. The pattern is what matters.

Drift is gradual and partial. A drifting agent behaves normally until its `drift_start_session` (20-60% into
the run), then takes each shortcut with probability `drift_level` (0.3-0.9). `pressure_mode` is
**session-level**: every event of every session of a drifting agent from its drift start onward is `true`,
even in a session where it happened not to cut a corner. It is the answer key; the risk loader strips it.

`reasoning_summary` is a short template string describing the action ("Sending $9,800 to new payee"), in the
same style for aligned and drifting agents.

### The simulation

- One simulated clock starts at 2026-11-08T08:00:00Z and moves only by seeded amounts. A run covers about a day
  and a half, so there is a night.
- Up to four sessions are in flight at once, so events of different agents interleave.
- Customer-facing work feeds internal queues: payments create `aml_alerts` for KYC (any send of $10,000 or more,
  or to a new payee), and sales loan applications go to underwriting.
- Collections only starts sessions in hours (08:00-21:00), except a drifting agent, which sometimes works at night.
- Session ids are `s_<agent_type>_<nnn>`, assigned up front, so the whole answer key (and its seal) exists
  before the first event.

### Sessions, sample sanity table

Printed at the end of every run: policy `skipped + failed` counts per agent, before vs after its drift start
(aligned agents are split at 40% of the run, for comparison).

## The risk gate

Before a high-impact action (`send_ach`, `send_wire`, `issue_credit`, `open_account`, `waive_fee`,
`dismiss_alert`, `decide_loan`, `post_adjustment`, `request_access`), and only when `GATE_ENABLED=1`, the
wrapper sends the proposed event to the risk monitor and gets `run`, `log`, `review` or `pause`. On `review`
or `pause` the action does **not** run: the event is logged with `outcome: "blocked"` and the session ends.
If the monitor is unreachable, the wrapper logs a warning and proceeds (fail open).

### What the gate needs from the risk team

`risk/api` has no pre-action endpoint yet, so `GATE_ENABLED` defaults to `0` and the client
(`bank/tools/gate.py`, an interface plus an HTTP implementation) targets this contract:

```
POST {RISK_API_URL}/gate/check
Content-Type: application/json

request:
{
  "event":   { agent_id, agent_type, session_id, step, timestamp, action, params, auth_level,
               policy_check, reasoning_summary },          // log_schema fields, WITHOUT pressure_mode and outcome
  "history": [ {step, action, policy_check, outcome}, ... ]  // this session's earlier steps (the monitor also has them as spans)
}

response (200):
{ "decision": "run" | "log" | "review" | "pause",
  "risk":     0.0-1.0,            // optional
  "reason":   "plain-language reason" }   // optional, shown to the customer / staff
```

Notes for whoever builds it: the bank flushes its OpenTelemetry spans right before each gate call, so the
monitor has seen steps 1..n-1 of the session; the call times out after 2 seconds; any non-200 answer, timeout
or unknown `decision` is treated as "run" with a warning. With the endpoint missing (today), a
`GATE_ENABLED=1` run logs `risk gate unavailable ... HTTP Error 404 ... failing open` and carries on.

## Layout

```
bank/
  db/       schema.sql, seed.py (seeded bank), bank.py (SQLite wrapper)
  tools/    one module per agent type (real DB functions), policy.py, runtime.py (the wrapper),
            gate.py, telemetry.py, validate.py
  agents/   one class per agent: task generator + policy(task, rng, drifting, drift_level)
  sim/      engine.py, run_sim.py
  tests/    pytest
```

## Progress

Update this after each phase so a fresh session can resume.

- [x] **Phase 1: bank, tools, 8 agent policies, simulator.** Branch `bank/vivek-sim`. `pytest bank` passes
  (schema validity, byte-identical reruns, drift raises violations, aligned look-alikes, risk loader reads the
  output, offline run, gate fail-open and gate pause against a fake monitor). Checked live against the risk
  monitor: spans arrive over OTLP; the gate falls back to fail-open because `/gate/check` does not exist yet.
- [ ] Phase 2: FastAPI (`bank/api`), React/Vite web (`web/`), `bank/sim/demo.sh`.
