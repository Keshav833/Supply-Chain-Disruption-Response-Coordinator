"""Production orchestrator (PHASE_6).

Workflow for one incident (one shared Blackboard, one run_id):
    1. Validate the input schema -> on failure: outcome "rejected", one log entry, stop.
    2. Create the Blackboard (incident is read-only, run_id = execution_id).
    3. MonitorAgent -> if it FAILED or the incident is invalid: stop safely (UNKNOWN).
    4. Inventory, Supplier and Route agents run concurrently (asyncio.gather, per-agent timeout).
       Each failure is recorded on the Blackboard; other agents' evidence is kept as published.
    5. CoordinatorAgent runs only after all three investigations have finished.
    6. Independent final gate: the decision is re-validated against Blackboard evidence with the
       deterministic validator (validate_mitigation_decision). A rejected decision -> outcome "failed".
    7. The complete trace is persisted ONCE to the append-only JSONL log; errors are reported.
    8. A structured IncidentResponse is returned.

The orchestrator contains no business logic: evidence comes from the agents, the decision from
the Coordinator, and validation from the Coordinator's deterministic validator. Decisions are
recommendations only (authorized is always False).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Union

from pydantic import BaseModel, Field, ValidationError

from supply_chain import config
from supply_chain.agents.base import BaseAgent
from supply_chain.agents.coordinator_agent import CoordinatorAgent
from supply_chain.agents.inventory_agent import InventoryAgent
from supply_chain.agents.monitor_agent import MonitorAgent
from supply_chain.agents.route_agent import RouteAgent
from supply_chain.agents.supplier_agent import SupplierAgent
from supply_chain.blackboard import Blackboard, append_jsonl
from supply_chain.contracts import AgentName, AgentOutput
from supply_chain.llm.interpreter import DeterministicInterpreter
from supply_chain.schemas import (
    AgentLogEntry,
    AgentStatus,
    DisruptionIncident,
    ExecutionMode,
    MitigationDecision,
    SupplyChainDataset,
)
from supply_chain.tools.coordinator_tools import read_blackboard_evidence, validate_mitigation_decision

logger = logging.getLogger(__name__)

Outcome = Literal["decided", "undetermined", "rejected", "failed"]


class IncidentResponse(BaseModel):
    """Structured result of one orchestrated run."""
    run_id: str
    incident_id: Optional[str]
    outcome: Outcome
    incident: Optional[Dict[str, Any]]
    decision: MitigationDecision
    selected_option_id: Optional[str] = None
    justification: str = Field(..., min_length=1)          # deterministic reasoning
    interpretation: Optional[str] = None                   # interpreter prose (never used for decisions)
    authorized: Literal[False] = False
    llm_used: bool = False
    agent_execution_modes: Dict[str, ExecutionMode] = Field(default_factory=dict)
    agent_status: Dict[str, AgentStatus] = Field(default_factory=dict)
    agent_errors: Dict[str, str] = Field(default_factory=dict)
    tools_invoked: Dict[str, List[str]] = Field(default_factory=dict)
    evidence: Dict[str, Any] = Field(default_factory=dict)
    evidence_status: Dict[str, str] = Field(default_factory=dict)
    notifications: List[Dict[str, Any]] = Field(default_factory=list)
    final_validation: Optional[Dict[str, Any]] = None
    errors: List[str] = Field(default_factory=list)
    trace: List[Dict[str, Any]] = Field(default_factory=list)
    log_path: Optional[str] = None
    log_persisted: bool = False
    log_entries_written: int = 0
    log_error: Optional[str] = None
    duration_ms: float = 0.0

    @property
    def execution_label(self) -> str:
        if not self.llm_used:
            return "deterministic_fallback (no live LLM call succeeded)"
        live = sorted(a for a, m in self.agent_execution_modes.items() if m == ExecutionMode.SEMANTIC_KERNEL_LLM)
        return f"semantic_kernel_llm for {live}; deterministic tools/validation for all agents"


@dataclass
class OrchestrationRun:
    response: IncidentResponse
    blackboard: Optional[Blackboard]
    outputs: Dict[AgentName, AgentOutput] = field(default_factory=dict)


class SupplyChainOrchestrator:
    """Runs the five-agent workflow. Agents can be injected (tests, alternative data sources)."""

    def __init__(self, dataset: Optional[SupplyChainDataset] = None, interpreter: Any = None,
                 log_path: Optional[Union[str, Path]] = None, agent_timeout: Optional[float] = None,
                 monitor: Optional[BaseAgent] = None, specialists: Optional[Sequence[BaseAgent]] = None,
                 coordinator: Optional[BaseAgent] = None):
        self.interpreter = interpreter or DeterministicInterpreter()
        self.log_path = Path(log_path) if log_path is not None else config.INCIDENT_LOG_PATH
        self.agent_timeout = agent_timeout if agent_timeout is not None else config.AGENT_TIMEOUT_SECONDS
        self.monitor = monitor or MonitorAgent(self.interpreter, dataset)
        self.specialists = list(specialists) if specialists is not None else [
            InventoryAgent(self.interpreter, dataset), SupplierAgent(self.interpreter, dataset),
            RouteAgent(self.interpreter, dataset)]
        self.coordinator = coordinator or CoordinatorAgent(self.interpreter)
        names = [a.name for a in self.specialists]
        if len(set(names)) != len(names) or AgentName.MONITOR in names or AgentName.COORDINATOR in names:
            raise ValueError("specialists must be distinct investigation agents")

    async def run(self, incident: Union[DisruptionIncident, Dict[str, Any]]) -> IncidentResponse:
        return (await self.run_with_details(incident)).response

    async def run_with_details(self, incident: Union[DisruptionIncident, Dict[str, Any]]) -> OrchestrationRun:
        start = time.perf_counter()
        try:
            inc = incident if isinstance(incident, DisruptionIncident) else DisruptionIncident.model_validate(incident)
        except ValidationError as exc:
            return self._reject_schema(incident, exc, start)

        bb = Blackboard(inc)
        outputs: Dict[AgentName, AgentOutput] = {}
        errors: List[str] = []

        # Step 3: Monitor gate
        monitor_out = await self._run_agent(self.monitor, bb)
        if monitor_out is not None:
            outputs[AgentName.MONITOR] = monitor_out
        assessment = bb.incident_assessment
        if bb.agent_status[AgentName.MONITOR.value] != AgentStatus.COMPLETE or assessment is None:
            msg = f"MonitorAgent failed: {bb.errors.get(AgentName.MONITOR.value, 'no assessment published')}"
            await bb.record_event("workflow_stopped", {"reason": msg}, AgentStatus.FAILED, error=msg)
            return await self._finish(bb, outputs, "failed", MitigationDecision.UNKNOWN, None,
                                f"No decision: {msg}. Investigation was not started.", errors + [msg], start)
        if not assessment.is_valid:
            msg = assessment.validation_message
            await bb.record_event("workflow_stopped", {"reason": "incident failed validation", "detail": msg},
                                  AgentStatus.FAILED, error=msg)
            return await self._finish(bb, outputs, "rejected", MitigationDecision.UNKNOWN, None,
                                f"No decision: {msg}. Investigation was not started.", errors + [msg], start)

        # Step 4: concurrent investigation
        await bb.record_event("investigation_started", {"agents": [a.name.value for a in self.specialists]},
                              AgentStatus.RUNNING)
        results = await asyncio.gather(*(self._run_agent(a, bb) for a in self.specialists))
        for agent, out in zip(self.specialists, results):
            if out is not None:
                outputs[agent.name] = out
        for name in (AgentName.INVENTORY, AgentName.SUPPLIER, AgentName.ROUTE):
            if name.value in bb.errors:
                errors.append(bb.errors[name.value])
        await bb.record_event("investigation_finished",
                              {"agent_status": {a.name.value: bb.agent_status[a.name.value].value
                                                for a in self.specialists},
                               "evidence_status": {k: v.value for k, v in bb.evidence_status().items()}},
                              AgentStatus.COMPLETE)

        # Step 5: Coordinator (only after all investigations finished)
        coord_out = await self._run_agent(self.coordinator, bb)
        if coord_out is not None:
            outputs[AgentName.COORDINATOR] = coord_out
        decision = bb.mitigation_decision
        if decision is None:
            msg = f"CoordinatorAgent failed: {bb.errors.get(AgentName.COORDINATOR.value, 'no decision published')}"
            await bb.record_event("workflow_failed", {"reason": msg}, AgentStatus.FAILED, error=msg)
            return await self._finish(bb, outputs, "failed", MitigationDecision.UNKNOWN, None,
                                f"No decision: {msg}.", errors + [msg], start)

        # Step 6: independent final validation gate
        check = validate_mitigation_decision(decision.decision.value, decision.selected_option_id,
                                             read_blackboard_evidence(bb))
        validation = check.model_dump(mode="json")
        if not check.is_valid or decision.authorized:
            msg = f"final validation rejected {decision.decision.value}: {check.violations}"
            await bb.record_event("final_decision_rejected", validation, AgentStatus.FAILED, error=msg)
            return await self._finish(bb, outputs, "failed", MitigationDecision.UNKNOWN, None,
                                f"No decision: {msg}.", errors + [msg], start, validation)
        await bb.record_event("final_decision_validated", validation, AgentStatus.COMPLETE)
        outcome: Outcome = "undetermined" if decision.decision == MitigationDecision.UNKNOWN else "decided"
        return await self._finish(bb, outputs, outcome, decision.decision, decision.selected_option_id,
                            decision.justification, errors, start, validation)

    # ------------------------------------------------------------------ helpers
    async def _run_agent(self, agent: BaseAgent, bb: Blackboard) -> Optional[AgentOutput]:
        """Run one agent with a timeout. Unexpected errors become a recorded FAILED status."""
        try:
            return await asyncio.wait_for(agent.run(bb), timeout=self.agent_timeout)
        except asyncio.TimeoutError:
            error = f"{agent.name.value} failed: timed out after {self.agent_timeout}s"
        except Exception as exc:  # agent.run normally converts errors itself; this is a safety net
            error = f"{agent.name.value} failed: {type(exc).__name__}: {exc}"
        logger.error(error)
        if bb.agent_status[agent.name.value] != AgentStatus.COMPLETE:
            await bb.mark_failed(agent.name, error)
        return None

    async def _finish(self, bb: Blackboard, outputs: Dict[AgentName, AgentOutput], outcome: Outcome,
                decision: MitigationDecision, option: Optional[str], justification: str,
                errors: List[str], start: float, validation: Optional[Dict[str, Any]] = None) -> OrchestrationRun:
        await bb.record_event("run_completed",
                              {"outcome": outcome, "decision": decision.value, "selected_option_id": option},
                              AgentStatus.COMPLETE if outcome in ("decided", "undetermined") else AgentStatus.FAILED)
        written, log_error = 0, None
        try:
            written = bb.persist_run_log(self.log_path)
        except OSError as exc:
            log_error = f"run log NOT persisted to {self.log_path}: {type(exc).__name__}: {exc}"
            logger.error(log_error)
        state = bb.to_dict()
        coord = outputs.get(AgentName.COORDINATOR)
        modes = dict(bb.agent_mode)
        response = IncidentResponse(
            run_id=bb.execution_id, incident_id=bb.incident_id, outcome=outcome, incident=state["incident"],
            decision=decision, selected_option_id=option, justification=justification,
            interpretation=coord.interpretation if coord is not None and outcome in ("decided", "undetermined")
            else None,
            llm_used=any(m == ExecutionMode.SEMANTIC_KERNEL_LLM for m in modes.values()),
            agent_execution_modes=modes, agent_status=dict(bb.agent_status), agent_errors=dict(bb.errors),
            tools_invoked=state["tools_invoked"],
            evidence={k: state[k] for k in ("incident_assessment", "inventory_findings", "supplier_findings",
                                            "route_findings", "mitigation_decision")},
            evidence_status=state["evidence_status"], notifications=state["notifications"],
            final_validation=validation, errors=errors + ([log_error] if log_error else []), trace=bb.trace(),
            log_path=str(self.log_path), log_persisted=log_error is None and written > 0,
            log_entries_written=written, log_error=log_error,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
        return OrchestrationRun(response=response, blackboard=bb, outputs=outputs)

    def _reject_schema(self, raw: Any, exc: ValidationError, start: float) -> OrchestrationRun:
        run_id = uuid.uuid4().hex
        incident_id = raw.get("incident_id") if isinstance(raw, dict) else None
        incident_id = incident_id if isinstance(incident_id, str) else None
        problems = [f"{'.'.join(map(str, e['loc'])) or 'report'}: {e['msg']}" for e in exc.errors()]
        msg = "incident report failed schema validation: " + "; ".join(problems)
        entry = AgentLogEntry(agent_name="Orchestrator", action="incident_rejected", section_updated="run",
                              evidence={"errors": problems}, status=AgentStatus.FAILED, error=msg,
                              incident_id=incident_id, execution_id=run_id, sequence=0)
        log_error = None
        try:
            append_jsonl(self.log_path, [entry])
        except OSError as e:
            log_error = f"run log NOT persisted to {self.log_path}: {type(e).__name__}: {e}"
        response = IncidentResponse(
            run_id=run_id, incident_id=incident_id, outcome="rejected",
            incident=raw if isinstance(raw, dict) else None, decision=MitigationDecision.UNKNOWN,
            justification=f"No decision: {msg}.", errors=[msg] + ([log_error] if log_error else []),
            trace=[entry.model_dump(mode="json")], log_path=str(self.log_path), log_persisted=log_error is None,
            log_entries_written=0 if log_error else 1, log_error=log_error,
            duration_ms=round((time.perf_counter() - start) * 1000, 1),
        )
        return OrchestrationRun(response=response, blackboard=None)


async def run_incident(incident: Union[DisruptionIncident, Dict[str, Any]], **kwargs: Any) -> IncidentResponse:
    """Convenience wrapper: build an orchestrator and run one incident."""
    return await SupplyChainOrchestrator(**kwargs).run(incident)
