"""
Shared Blackboard (PHASE_3).

One Blackboard instance is created per incident and passed to all five agents.

Ownership rules (enforced on every write):
    incident              -> set once at construction (the input report, read-only)
    incident_assessment   -> MonitorAgent
    inventory_findings    -> InventoryAgent
    supplier_findings     -> SupplierAgent
    route_findings        -> RouteAgent
    mitigation_decision   -> CoordinatorAgent (also owns `notifications`)

Write semantics:
    * an agent may write only its own section, only while RUNNING, only once;
    * the payload type must match the section and belong to this incident;
    * a FAILED agent records an error and NO evidence (its section stays None).

Read semantics:
    * snapshot_evidence() returns an immutable BlackboardEvidence containing only
      published sections. A section that was never published is UNKNOWN; a
      section whose owner failed is FAILED. Neither is a negative result.

Logging:
    * every state change is appended to an in-memory trace with a sequence number
      and the run's execution_id;
    * persist_run_log() appends only entries not yet written (append-only JSONL;
      calling it twice never duplicates or rewrites lines).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from supply_chain.contracts import BLACKBOARD_SECTION, AgentName, BlackboardEvidence
from supply_chain.schemas import (
    AgentLogEntry,
    AgentStatus,
    DisruptionIncident,
    EvidenceStatus,
    ExecutionMode,
    IncidentAssessment,
    InventoryFindings,
    MitigationResult,
    Notification,
    RouteFindings,
    SupplierFindings,
)

logger = logging.getLogger(__name__)


class BlackboardError(Exception):
    """Base class for Blackboard rule violations."""


class OwnershipError(BlackboardError, PermissionError):
    """An agent tried to write a section it does not own."""


class BlackboardStateError(BlackboardError):
    """Invalid state transition (e.g. writing twice, writing while not RUNNING)."""


SECTION_OWNER: Dict[str, AgentName] = {section: agent for agent, section in BLACKBOARD_SECTION.items()}

SECTION_TYPE: Dict[str, Type[BaseModel]] = {
    "incident_assessment": IncidentAssessment,
    "inventory_findings": InventoryFindings,
    "supplier_findings": SupplierFindings,
    "route_findings": RouteFindings,
    "mitigation_decision": MitigationResult,
}

EVIDENCE_SECTIONS = ("incident_assessment", "inventory_findings", "supplier_findings", "route_findings")
_LOG_WRITE_LOCK = threading.Lock()


def append_jsonl(log_path: Path, entries: List[AgentLogEntry]) -> None:
    """Append entries as one locked write (never truncates or interleaves in this process)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(e.model_dump_json() + "\n" for e in entries)
    with _LOG_WRITE_LOCK:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(data)
            f.flush()


class Blackboard:
    """Shared, ownership-enforced state for one incident run."""

    def __init__(self, incident: DisruptionIncident, execution_id: Optional[str] = None):
        if not isinstance(incident, DisruptionIncident):
            raise TypeError("Blackboard requires a validated DisruptionIncident")
        self.incident: DisruptionIncident = incident
        self.incident_id: str = incident.incident_id
        self.execution_id: str = execution_id or uuid.uuid4().hex
        self._sections: Dict[str, Optional[BaseModel]] = {s: None for s in SECTION_TYPE}
        self.notifications: List[Notification] = []
        self.agent_status: Dict[str, AgentStatus] = {a.value: AgentStatus.PENDING for a in AgentName}
        self.agent_mode: Dict[str, ExecutionMode] = {}
        self.tools_invoked: Dict[str, List[str]] = {a.value: [] for a in AgentName}
        self.errors: Dict[str, str] = {}
        self.run_log: List[AgentLogEntry] = []
        self._persisted = 0
        self._lock = asyncio.Lock()
        self._log("Orchestrator", "incident_received", "incident",
                  incident.model_dump(mode="json"), AgentStatus.COMPLETE)

    # ------------------------------------------------------------------ reads
    def __getattr__(self, name: str) -> Any:
        sections = self.__dict__.get("_sections")
        if sections is not None and name in sections:
            return sections[name]
        raise AttributeError(name)

    def get_section(self, section: str) -> Optional[BaseModel]:
        if section not in self._sections:
            raise KeyError(f"unknown Blackboard section '{section}'")
        return self._sections[section]

    def evidence_status(self) -> Dict[str, EvidenceStatus]:
        return self.snapshot_evidence().evidence_status()

    def snapshot_evidence(self) -> BlackboardEvidence:
        """Immutable copy of PUBLISHED evidence only (what the Coordinator may read)."""
        return BlackboardEvidence(
            incident=self.incident.model_copy(deep=True),
            agent_status=dict(self.agent_status),
            **{s: (self._sections[s].model_copy(deep=True) if self._sections[s] else None)
               for s in EVIDENCE_SECTIONS},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "execution_id": self.execution_id,
            "incident": self.incident.model_dump(mode="json"),
            **{s: (m.model_dump(mode="json") if m else None) for s, m in self._sections.items()},
            "notifications": [n.model_dump(mode="json") for n in self.notifications],
            "evidence_status": {k: v.value for k, v in self.evidence_status().items()},
            "agent_status": {k: v.value for k, v in self.agent_status.items()},
            "agent_execution_mode": {k: v.value for k, v in self.agent_mode.items()},
            "tools_invoked": {k: list(v) for k, v in self.tools_invoked.items()},
            "errors": dict(self.errors),
        }

    # ----------------------------------------------------------------- writes
    async def mark_running(self, agent: AgentName) -> None:
        agent = AgentName(agent)
        async with self._lock:
            if self.agent_status[agent.value] != AgentStatus.PENDING:
                raise BlackboardStateError(
                    f"{agent.value} cannot start from status {self.agent_status[agent.value].value}")
            self.agent_status[agent.value] = AgentStatus.RUNNING
            self._log(agent.value, "agent_started", "agent_status", {}, AgentStatus.RUNNING)

    async def record_tool_call(self, agent: AgentName, tool: str, ok: bool,
                               detail: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
        agent = AgentName(agent)
        async with self._lock:
            if self.agent_status[agent.value] != AgentStatus.RUNNING:
                raise BlackboardStateError(f"{agent.value} is not RUNNING; cannot record tool calls")
            if ok:
                self.tools_invoked[agent.value].append(tool)
            self._log(agent.value, f"tool_call:{tool}", "tools_invoked", detail or {},
                      AgentStatus.RUNNING, error=error)

    async def record_tool_retry(self, agent: AgentName, tool: str, attempt: int,
                                max_attempts: int, error: str) -> None:
        """Record a bounded retry without treating a failed attempt as successful tool use."""
        agent = AgentName(agent)
        async with self._lock:
            if self.agent_status[agent.value] != AgentStatus.RUNNING:
                raise BlackboardStateError(f"{agent.value} is not RUNNING; cannot record tool retries")
            self._log(agent.value, f"tool_retry:{tool}", "tools_invoked",
                      {"attempt": attempt, "max_attempts": max_attempts}, AgentStatus.RUNNING, error=error)

    async def publish(self, agent: AgentName, section: str, payload: BaseModel,
                      execution_mode: ExecutionMode = ExecutionMode.DETERMINISTIC_FALLBACK,
                      notifications: Optional[List[Notification]] = None) -> None:
        """Write `payload` to `section` on behalf of `agent` and mark the agent COMPLETE."""
        agent = AgentName(agent)
        execution_mode = ExecutionMode(execution_mode)
        async with self._lock:
            owner = SECTION_OWNER.get(section)
            if owner is None:
                raise OwnershipError(f"section '{section}' is not writable by agents")
            if owner != agent:
                raise OwnershipError(f"{agent.value} cannot write '{section}' (owned by {owner.value})")
            expected = SECTION_TYPE[section]
            if not isinstance(payload, expected):
                raise TypeError(f"'{section}' requires {expected.__name__}, got {type(payload).__name__}")
            if self.agent_status[agent.value] != AgentStatus.RUNNING:
                raise BlackboardStateError(
                    f"{agent.value} must be RUNNING to publish (is {self.agent_status[agent.value].value})")
            if self._sections[section] is not None:
                raise BlackboardStateError(f"'{section}' is already published (write-once)")
            pid = getattr(payload, "incident_id", self.incident_id)
            if pid != self.incident_id:
                raise BlackboardStateError(f"payload belongs to {pid}, not {self.incident_id}")
            if notifications and agent != AgentName.COORDINATOR:
                raise OwnershipError("only CoordinatorAgent may publish notifications")
            payload = expected.model_validate(payload.model_dump())  # re-validate, detach from caller
            self._sections[section] = payload
            if notifications:
                self.notifications = [Notification.model_validate(n.model_dump()) for n in notifications]
            self.agent_status[agent.value] = AgentStatus.COMPLETE
            self.agent_mode[agent.value] = execution_mode
            self._log(agent.value, "evidence_published", section, payload.model_dump(mode="json"),
                      AgentStatus.COMPLETE, mode=execution_mode)

    async def mark_failed(self, agent: AgentName, error: str) -> None:
        """Record a failure. The agent's section stays empty (no fabricated evidence)."""
        agent = AgentName(agent)
        if not error or not error.strip():
            raise ValueError("a failure must carry an error message")
        async with self._lock:
            if self.agent_status[agent.value] == AgentStatus.COMPLETE:
                raise BlackboardStateError(f"{agent.value} already COMPLETE; cannot mark FAILED")
            self.agent_status[agent.value] = AgentStatus.FAILED
            self.errors[agent.value] = error
            self._log(agent.value, "agent_failed", BLACKBOARD_SECTION[agent], {},
                      AgentStatus.FAILED, error=error)

    # ---------------------------------------------------------------- logging
    def _log(self, agent_name: str, action: str, section: str, evidence: Dict[str, Any],
             status: AgentStatus, error: Optional[str] = None,
             mode: Optional[ExecutionMode] = None) -> None:
        entry = AgentLogEntry(
            agent_name=agent_name, action=action, section_updated=section, evidence=evidence,
            status=status, error=error, incident_id=self.incident_id,
            execution_id=self.execution_id, sequence=len(self.run_log), execution_mode=mode,
        )
        self.run_log.append(entry)
        logger.debug("[Blackboard %s] %s %s", self.incident_id, agent_name, action)

    async def record_event(self, action: str, details: Dict[str, Any], status: AgentStatus,
                           error: Optional[str] = None) -> None:
        """Orchestrator events (gates, skips, final validation). Never writes a section."""
        async with self._lock:
            self._log("Orchestrator", action, "run", details, AgentStatus(status), error=error)

    @property
    def persisted_count(self) -> int:
        return self._persisted

    def persist_run_log(self, log_path: Path) -> int:
        """Append not-yet-persisted entries to a JSONL file. Returns lines written.

        Raises OSError on failure; nothing is marked persisted unless the write succeeded."""
        pending = self.run_log[self._persisted:]
        if not pending:
            return 0
        append_jsonl(Path(log_path), pending)
        self._persisted += len(pending)
        return len(pending)

    def trace(self) -> List[Dict[str, Any]]:
        return [json.loads(e.model_dump_json()) for e in self.run_log]
