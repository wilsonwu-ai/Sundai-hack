"""The trace monitor's API: OTLP ingest, the events API, per-agent views and risk."""

import logging
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool

from risk.behaviorgpt.history import to_history
from risk.ingest.events import check, from_span
from risk.ingest.load_logs import LogValidationError
from risk.ingest.otlp import JSON, PROTOBUF, OTLPDecodeError, decode_request, encode_response, iter_spans, media_type
from risk.scoring.detector import Detector, NoDetector
from risk.store import Store, by_session

log = logging.getLogger("risk.api")

EXAMPLE_EVENT = {
    "agent_id": "payments-01",
    "agent_type": "payments",
    "session_id": "s_001",
    "step": 1,
    "timestamp": "2026-11-08T14:00:05Z",
    "action": "add_beneficiary",
    "params": {"customer_id": "C-10421", "beneficiary_name": "Northside Property Mgmt"},
    "auth_level": "customer_verified",
    "policy_check": "passed",
    "outcome": "success",
    "pressure_mode": False,
    "reasoning_summary": "Customer asked to add their landlord as a payee for rent.",
}


def create_app(db_path: str | Path, detector: Detector | None = None) -> FastAPI:
    store = Store(db_path)
    detector = detector or NoDetector()
    app = FastAPI(
        title="Sundai Bank risk monitor",
        version="0.2.0",
        description=(
            "Collects agent actions (shared/log_schema.json) over OTLP/HTTP or POST /events "
            "and scores each agent across its sessions."
        ),
    )
    app.state.store = store

    def agent_or_404(agent_id: str) -> dict[str, list[dict]]:
        sessions = by_session(store.events(agent_id=agent_id))
        if not sessions:
            raise HTTPException(404, f"no events for agent {agent_id!r}")
        return sessions

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict:
        return {"ok": True}

    # -- ingest -----------------------------------------------------------------

    @app.post("/v1/traces", tags=["ingest"], summary="OTLP/HTTP trace export (protobuf or JSON)")
    async def otlp_traces(request: Request) -> Response:
        """One span per action, the event's fields as attributes. Spans that aren't
        actions are skipped; actions that fail validation are reported back as rejected."""
        content_type = request.headers.get("content-type")
        if media_type(content_type) not in (PROTOBUF, JSON):
            return Response(f"unsupported content-type {content_type}", status_code=415)
        try:
            req = decode_request(
                await request.body(), content_type, request.headers.get("content-encoding")
            )
        except OTLPDecodeError as exc:
            return Response(str(exc), status_code=400, media_type="text/plain")

        events, otel_ids, rejected = [], [], []
        for span in iter_spans(req):
            event = from_span(span)
            if event is None:
                continue
            try:
                events.append(check(event, f"span {span['span_id']}"))
                otel_ids.append((span["trace_id"], span["span_id"]))
            except LogValidationError as exc:
                rejected.append(str(exc))
        if events:
            await run_in_threadpool(store.add, events, source="otel", otel_ids=otel_ids)
        if rejected:
            log.warning("rejected %d span(s): %s", len(rejected), "; ".join(rejected[:3]))
        body, mt = encode_response(content_type, len(rejected), "; ".join(rejected[:5]))
        return Response(body, media_type=mt)

    # -- events API -------------------------------------------------------------

    @app.post("/events", tags=["events"])
    def post_events(
        body: dict[str, Any] | list[dict[str, Any]] = Body(examples=[EXAMPLE_EVENT]),
    ) -> dict:
        """Record one log event or a list (shared/log_schema.json, as in the .jsonl logs).
        All or nothing: one invalid event rejects the request."""
        items = body if isinstance(body, list) else [body]
        try:
            events = [check(e, f"event {i}") for i, e in enumerate(items)]
        except LogValidationError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"ids": store.add(events, source="api")}

    @app.get("/events", tags=["events"])
    def list_events(
        agent_id: str | None = None,
        agent_type: str | None = None,
        session_id: str | None = None,
        action: str | None = None,
        limit: int = Query(500, ge=1, le=5000),
        offset: int = Query(0, ge=0),
    ) -> list[dict]:
        """Events without `pressure_mode`, each session's together in step order."""
        return store.events(
            agent_id=agent_id, agent_type=agent_type, session_id=session_id,
            action=action, limit=limit, offset=offset,
        )

    # -- agents and sessions ----------------------------------------------------

    @app.get("/agents", tags=["agents"])
    def list_agents() -> list[dict]:
        """Every agent: sessions, events, skipped policy checks, blocked/errored and elevated actions."""
        return store.agents()

    @app.get("/agents/{agent_id}", tags=["agents"])
    def get_agent(agent_id: str) -> dict:
        agent = store.agent(agent_id)
        if agent is None:
            raise HTTPException(404, f"no events for agent {agent_id!r}")
        return agent

    @app.get("/agents/{agent_id}/history", tags=["agents"])
    def agent_history(agent_id: str) -> dict:
        """The agent's sessions as BehaviorGPT histories."""
        return {
            "agent_id": agent_id,
            "sessions": [
                {"session_id": sid, "history": to_history(s)}
                for sid, s in agent_or_404(agent_id).items()
            ],
        }

    @app.get("/agents/{agent_id}/risk", tags=["agents"])
    def agent_risk(agent_id: str) -> dict:
        return detector.score_agent(agent_id, agent_or_404(agent_id))

    @app.get("/sessions/{session_id}", tags=["sessions"])
    def get_session(session_id: str) -> list[dict]:
        events = store.events(session_id=session_id)
        if not events:
            raise HTTPException(404, f"no events for session {session_id!r}")
        return events

    @app.get("/eval/labels", tags=["eval"])
    def labels(agent_id: str | None = None) -> dict[str, bool]:
        """Ground truth, session_id -> pressure_mode. For scoring a detector, never as its input."""
        return store.labels(agent_id)

    return app
