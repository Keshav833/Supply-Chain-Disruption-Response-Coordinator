"""FastAPI integration for the Supply Chain Disruption Response Coordinator.

Routes deliberately contain no agent or mitigation logic.  They validate public
input, call the production orchestrator, and retain a safe, in-memory view of
each completed run for the result endpoints.  The orchestrator creates a new
Blackboard for every invocation, including concurrent invocations.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from supply_chain.data.sample_data import SAMPLE_INCIDENTS
from supply_chain.orchestrator import IncidentResponse, OrchestrationRun, SupplyChainOrchestrator
from supply_chain.schemas import DisruptionIncident

logger = logging.getLogger(__name__)


class SampleIncidentSummary(BaseModel):
    """Public, non-sensitive summary of a bundled incident."""

    incident_id: str
    disruption_type: str
    affected_product_id: str
    affected_warehouse_id: str
    required_quantity: int
    deadline_days: int
    description: str


class LogPersistenceStatus(BaseModel):
    persisted: bool
    entries_written: int = Field(ge=0)


class PublicRunResponse(BaseModel):
    """Safe API projection of an IncidentResponse (never exposes paths or raw errors)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    incident_id: Optional[str]
    outcome: str
    incident: Optional[Dict[str, Any]]
    agent_status: Dict[str, str]
    agent_execution_modes: Dict[str, str]
    tools_invoked: Dict[str, List[str]]
    evidence_status: Dict[str, str]
    evidence: Dict[str, Any]
    mitigation_decision: str
    selected_option_id: Optional[str]
    justification: str
    notifications: List[Dict[str, Any]]
    authorized: bool
    execution_trace: List[Dict[str, Any]]
    log_persistence: LogPersistenceStatus
    duration_ms: float


class BlackboardResponse(BaseModel):
    run_id: str
    incident_id: str
    blackboard: Dict[str, Any]


class TraceResponse(BaseModel):
    run_id: str
    incident_id: str
    execution_trace: List[Dict[str, Any]]


class DecisionResponse(BaseModel):
    run_id: str
    incident_id: Optional[str]
    outcome: str
    mitigation_decision: str
    selected_option_id: Optional[str]
    justification: str
    notifications: List[Dict[str, Any]]
    authorized: bool
    final_validation: Optional[Dict[str, Any]]


def _public_response(response: IncidentResponse) -> PublicRunResponse:
    """Project an internal run response to fields safe for an HTTP client."""
    return PublicRunResponse(
        run_id=response.run_id,
        incident_id=response.incident_id,
        outcome=response.outcome,
        incident=response.incident,
        agent_status={name: value.value for name, value in response.agent_status.items()},
        agent_execution_modes={name: value.value for name, value in response.agent_execution_modes.items()},
        tools_invoked=response.tools_invoked,
        evidence_status=response.evidence_status,
        evidence=response.evidence,
        mitigation_decision=response.decision.value,
        selected_option_id=response.selected_option_id,
        justification=response.justification,
        notifications=response.notifications,
        authorized=response.authorized,
        execution_trace=response.trace,
        log_persistence=LogPersistenceStatus(
            persisted=response.log_persisted,
            entries_written=response.log_entries_written,
        ),
        duration_ms=response.duration_ms,
    )


@dataclass
class StoredRun:
    response: PublicRunResponse
    blackboard: Optional[Dict[str, Any]]
    final_validation: Optional[Dict[str, Any]]


class RunStore:
    """Small process-local run registry used only by result retrieval endpoints."""

    def __init__(self) -> None:
        self._by_id: Dict[str, StoredRun] = {}
        self._by_incident: Dict[str, List[str]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def add(self, run: OrchestrationRun) -> PublicRunResponse:
        public = _public_response(run.response)
        board = run.blackboard.to_dict() if run.blackboard is not None else None
        stored = StoredRun(public, board, run.response.final_validation)
        async with self._lock:
            self._by_id[public.run_id] = stored
            if public.incident_id:
                self._by_incident[public.incident_id].append(public.run_id)
        return public

    async def get(self, incident_id: str, run_id: Optional[str]) -> StoredRun:
        async with self._lock:
            selected = run_id
            if selected is None:
                runs = self._by_incident.get(incident_id, [])
                selected = runs[-1] if runs else None
            stored = self._by_id.get(selected) if selected else None
            if stored is None or stored.response.incident_id != incident_id:
                raise KeyError(incident_id)
            return stored


OrchestratorFactory = Callable[[], SupplyChainOrchestrator]


def create_app(orchestrator_factory: Optional[OrchestratorFactory] = None) -> FastAPI:
    """Create an independently testable FastAPI application."""
    from supply_chain import config
    config.configure_logging()
    app = FastAPI(
        title="Supply Chain Disruption Response Coordinator API",
        version="0.8.0",
        description="API facade over the five-agent Blackboard orchestrator.",
    )
    app.state.run_store = RunStore()
    app.state.orchestrator_factory = orchestrator_factory or SupplyChainOrchestrator

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic's raw input may contain secrets; return field/message only.
        problems = [
            {"field": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            content={"detail": "Invalid request.", "errors": problems})

    @app.exception_handler(Exception)
    async def unhandled_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error", exc_info=exc)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            content={"detail": "Internal server error."})

    async def execute(incident: DisruptionIncident) -> PublicRunResponse:
        # A fresh orchestrator means fresh agent instances and exactly one new Blackboard per request.
        orchestrator = app.state.orchestrator_factory()
        run = await orchestrator.run_with_details(incident)
        return await app.state.run_store.add(run)

    async def stored_run(incident_id: str, run_id: Optional[str]) -> StoredRun:
        try:
            return await app.state.run_store.get(incident_id, run_id)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="No analysis run was found for this incident.") from None

    @app.get("/api/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok", "service": "supply-chain-disruption-response-coordinator"}

    @app.get("/api/incidents/", response_model=List[SampleIncidentSummary])
    async def list_incidents() -> List[SampleIncidentSummary]:
        return [SampleIncidentSummary.model_validate(incident) for incident in SAMPLE_INCIDENTS]

    @app.get("/api/incidents/{incident_id}", response_model=DisruptionIncident)
    async def get_incident(incident_id: str) -> DisruptionIncident:
        for incident in SAMPLE_INCIDENTS:
            if incident["incident_id"] == incident_id:
                return DisruptionIncident.model_validate(incident)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sample incident not found.")

    @app.post("/api/incidents/", response_model=PublicRunResponse)
    async def process_incident(incident: DisruptionIncident) -> PublicRunResponse:
        return await execute(incident)

    @app.post("/api/incidents/{incident_id}/analyze", response_model=PublicRunResponse)
    async def analyze_sample_incident(incident_id: str) -> PublicRunResponse:
        incident = await get_incident(incident_id)
        return await execute(incident)

    @app.get("/api/incidents/{incident_id}/blackboard", response_model=BlackboardResponse)
    async def get_blackboard(incident_id: str, run_id: Optional[str] = Query(default=None)) -> BlackboardResponse:
        stored = await stored_run(incident_id, run_id)
        if stored.blackboard is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="This rejected request has no Blackboard state.")
        return BlackboardResponse(run_id=stored.response.run_id, incident_id=incident_id, blackboard=stored.blackboard)

    @app.get("/api/incidents/{incident_id}/trace", response_model=TraceResponse)
    async def get_trace(incident_id: str, run_id: Optional[str] = Query(default=None)) -> TraceResponse:
        stored = await stored_run(incident_id, run_id)
        return TraceResponse(run_id=stored.response.run_id, incident_id=incident_id,
                             execution_trace=stored.response.execution_trace)

    @app.get("/api/incidents/{incident_id}/decision", response_model=DecisionResponse)
    async def get_decision(incident_id: str, run_id: Optional[str] = Query(default=None)) -> DecisionResponse:
        stored = await stored_run(incident_id, run_id)
        response = stored.response
        return DecisionResponse(
            run_id=response.run_id, incident_id=response.incident_id, outcome=response.outcome,
            mitigation_decision=response.mitigation_decision, selected_option_id=response.selected_option_id,
            justification=response.justification, notifications=response.notifications,
            authorized=response.authorized, final_validation=stored.final_validation,
        )

    return app


app = create_app()
