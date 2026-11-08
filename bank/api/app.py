"""Sundai Bank API (port 8000): start and watch simulation runs, read the log, serve the customer site.

    .venv/bin/uvicorn bank.api.app:app --port 8000

Drift state is never exposed except by GET /sim/{run_id}/labels (the "Reveal" button). /logs strips
`pressure_mode` from every event for the same reason.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from bank.agents import AGENT_TYPES, CUSTOMER_FACING
from bank.sim.engine import DEFAULT_OUT, SimConfig, Simulation
from bank.tools import REGISTRY

DESCRIPTIONS = {
    "support": "Balances, card freezes, contact details and password resets for the customer in the session.",
    "payments": "Adds payees and sends ACH and wire payments, with 2FA for new payees.",
    "disputes": "Opens and investigates card disputes, credits valid ones, closes the case.",
    "sales": "Recommends products and opens accounts when the customer agrees.",
    "collections": "Contacts customers with overdue accounts, offers plans, waives small fees.",
    "kyc": "Works the AML alert queue: escalates, files SARs or dismisses.",
    "underwriting": "Pulls credit, verifies income, scores and decides loan applications.",
    "ops": "Reconciles the ledger, posts adjustments, reads logs, requests access.",
}

DEMO_PRESET = {"seed": 154, "sessions_per_agent": 40, "live": True, "force_drifters": ["payments", "kyc"],
               "gate": True, "otel": True, "demo": True}


class SimStart(BaseModel):
    seed: int = 42
    sessions_per_agent: int = Field(40, ge=1, le=500)
    live: bool = False
    live_rate: float = Field(2.0, gt=0, le=200)
    force_drifters: list[str] = []
    drifters: int | None = Field(None, ge=0, le=8)
    gate: bool | None = None
    otel: bool | None = None
    demo: bool = False


class CustomerRequest(BaseModel):
    agent_type: str
    task: str | dict[str, Any]


class RunManager:
    def __init__(self, out: Path):
        self.out = out
        self.runs: dict[str, Simulation] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.current: str | None = None
        self.lock = threading.RLock()
        self._sandbox()

    def _sandbox(self) -> None:
        """Before any run there is still a bank to bank with: seed 42, nobody drifts."""
        sim = Simulation(SimConfig(seed=42, sessions_per_agent=0, drifters=0, out=self.out, run_id="sandbox",
                                   otel=False, gate=False))
        sim.prepare()
        sim.state = "idle"
        self.runs["sandbox"] = sim
        self.current = "sandbox"

    def start(self, req: SimStart) -> Simulation:
        with self.lock:
            cfg = SimConfig(seed=req.seed, sessions_per_agent=req.sessions_per_agent, drifters=req.drifters,
                            force_drifters=req.force_drifters, live=req.live, live_rate=req.live_rate, out=self.out,
                            otel=req.otel, gate=req.gate, demo=req.demo)
            base = cfg.resolved_run_id()
            run_id, n = base, 1
            while run_id in self.runs:
                n += 1
                run_id = f"{base}-r{n}"
            cfg.run_id = run_id
            sim = Simulation(cfg)                    # raises ValueError on unknown forced agent types
            self._retire(self.current)
            sim.prepare()
            self.runs[run_id] = sim
            self.current = run_id
            t = threading.Thread(target=self._run, args=(sim,), name=f"sim-{run_id}", daemon=True)
            self.threads[run_id] = t
            t.start()
            return sim

    @staticmethod
    def _run(sim: Simulation) -> None:
        try:
            sim.run()
        except Exception:  # noqa: BLE001 - recorded on the sim; the status endpoint reports it
            pass

    def _retire(self, run_id: str | None) -> None:
        sim = self.runs.get(run_id or "")
        if sim is None or sim.state != "running":
            return
        sim.stop()
        t = self.threads.get(run_id)
        if t:
            t.join(timeout=10)

    def get(self, run_id: str | None) -> Simulation:
        sim = self.runs.get(run_id or self.current or "")
        if sim is None:
            raise HTTPException(404, f"unknown run {run_id!r}")
        return sim


def public_event(e: dict, seq: int) -> dict:
    out = {k: v for k, v in e.items() if k != "pressure_mode"}
    out["seq"] = seq
    return out


def create_app(out: str | Path | None = None) -> FastAPI:
    manager = RunManager(Path(out or os.environ.get("SIM_OUT", DEFAULT_OUT)))
    app = FastAPI(title="Sundai Bank agents API", version="0.1.0",
                  description="Start simulation runs, read the action log, and drive the customer site.")
    origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in origins], allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["X-Seal-Sha256"])
    app.state.manager = manager

    # -- runs ---------------------------------------------------------------------

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict:
        return {"ok": True, "current_run": manager.current}

    @app.post("/sim/start", tags=["sim"])
    def sim_start(req: SimStart) -> dict:
        try:
            sim = manager.start(req)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"run_id": sim.run_id, "seal": sim.seal}

    @app.post("/sim/demo", tags=["sim"])
    def sim_demo() -> dict:
        """The demo preset: live, fixed seed, payments + kyc drifting, OpenTelemetry and the gate on."""
        sim = manager.start(SimStart(**DEMO_PRESET))
        return {"run_id": sim.run_id, "seal": sim.seal}

    @app.get("/sim/{run_id}/status", tags=["sim"])
    def sim_status(run_id: str) -> dict:
        return manager.get(run_id).status()

    @app.post("/sim/{run_id}/stop", tags=["sim"])
    def sim_stop(run_id: str) -> dict:
        sim = manager.get(run_id)
        sim.stop()
        return sim.status()

    @app.get("/sim/{run_id}/seal", tags=["sim"])
    def sim_seal(run_id: str) -> dict:
        return {"sha256": manager.get(run_id).seal}

    @app.get("/sim/{run_id}/labels", tags=["sim"])
    def sim_labels(run_id: str) -> Response:
        """The answer key, as the exact bytes that were sealed (so a browser can hash them). Reveal button only."""
        sim = manager.get(run_id)
        raw = (sim.dir / "labels.json").read_bytes()
        return Response(raw, media_type="application/json", headers={"X-Seal-Sha256": sim.seal or ""})

    @app.get("/runs", tags=["sim"])
    def runs() -> dict:
        return {"current": manager.current, "runs": [s.status() for s in manager.runs.values()]}

    # -- the log --------------------------------------------------------------------

    @app.get("/logs", tags=["logs"])
    def logs(run_id: str | None = None, since: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=5000),
             tail: int | None = Query(None, ge=1, le=5000)) -> dict:
        """Events after `since` (an event count: pass back `next`), oldest first, blocked ones included.
        `pressure_mode` is never in here. `tail=N` starts from the last N events instead."""
        sim = manager.get(run_id)
        events = sim.events
        total = len(events)
        start = max(0, total - tail) if tail is not None else since
        chunk = events[start:start + limit]
        return {"run_id": sim.run_id, "state": sim.state, "next": start + len(chunk), "total": total,
                "events": [public_event(e, start + i + 1) for i, e in enumerate(chunk)]}

    @app.get("/agents", tags=["agents"])
    def agents() -> list[dict]:
        sim = manager.get(None)
        counts: dict[str, int] = {}
        blocked: dict[str, int] = {}
        last: dict[str, str] = {}
        for e in sim.events:
            counts[e["agent_type"]] = counts.get(e["agent_type"], 0) + 1
            blocked[e["agent_type"]] = blocked.get(e["agent_type"], 0) + (e["outcome"] == "blocked")
            last[e["agent_type"]] = e["action"]
        return [{"agent_id": f"{t}-01", "agent_type": t, "view": "customer" if t in CUSTOMER_FACING else "internal",
                 "description": DESCRIPTIONS[t], "actions": sorted(REGISTRY[t]), "events": counts.get(t, 0),
                 "blocked": blocked.get(t, 0), "last_action": last.get(t)} for t in AGENT_TYPES]

    # -- the customer site ------------------------------------------------------------

    @app.get("/customers", tags=["customer"])
    def customers() -> list[dict]:
        bank = manager.get(None).bank
        return [{"customer_id": c["customer_id"], "name": c["name"], "segment": c["segment"]} for c in bank.customers()]

    @app.get("/customers/{customer_id}", tags=["customer"])
    def customer(customer_id: str) -> dict:
        bank = manager.get(None).bank
        c = bank.customer(customer_id)
        if c is None:
            raise HTTPException(404, f"unknown customer {customer_id!r}")
        c.pop("annual_income", None)
        return {"customer": c, "accounts": bank.accounts(customer_id), "transactions": bank.transactions(customer_id, 40),
                "beneficiaries": bank.beneficiaries(customer_id), "cards": bank.cards(customer_id),
                "messages": bank.messages(customer_id, 30), "disputes": bank.disputes(customer_id)}

    @app.get("/products", tags=["customer"])
    def products() -> list[dict]:
        return manager.get(None).bank.products()

    @app.post("/customer/{customer_id}/request", tags=["customer"])
    def customer_request(customer_id: str, body: CustomerRequest = Body(...)) -> dict:
        """Run a customer-facing agent on a task the customer typed or clicked. Respects the current run's drift
        state and the risk gate. Returns the actions the agent took."""
        sim = manager.get(None)
        if body.agent_type not in CUSTOMER_FACING:
            raise HTTPException(422, f"agent_type must be one of {', '.join(CUSTOMER_FACING)}")
        if sim.bank.customer(customer_id) is None:
            raise HTTPException(404, f"unknown customer {customer_id!r}")
        request = {"text": body.task} if isinstance(body.task, str) else dict(body.task)
        request["customer_id"] = customer_id
        try:
            result = sim.run_interactive(body.agent_type, request)
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(422, f"could not understand that request: {exc}") from None
        actions = [{k: e[k] for k in ("step", "timestamp", "action", "params", "auth_level", "policy_check", "outcome",
                                      "reasoning_summary")} for e in result["events"]]
        done = [a for a in actions if a["outcome"] == "success"]
        message = ("Paused by risk check: a step was held for review and did not run." if result["blocked"]
                   else f"{len(done)} step{'s' if len(done) != 1 else ''} completed." if done else "Nothing was done.")
        return {"run_id": sim.run_id, "session_id": result["session_id"], "agent_id": result["agent_id"],
                "task": result["task"], "paused_by_risk": result["blocked"], "gate": result["gate"],
                "message": message, "actions": actions}

    return app

