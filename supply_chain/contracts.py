"""
Input/output contracts for the five agents.

Every agent:
  * receives a typed *Input* built from the shared Blackboard,
  * must invoke its two required Python tools,
  * writes exactly one Blackboard section,
  * returns a typed *Output*.

Output invariants (enforced by validators):
  * COMPLETE  -> payload present, no error, both required tools invoked.
  * FAILED    -> error message present and NO payload (a failure never becomes
                 an empty "successful" result or invented evidence).

The Coordinator's input is BlackboardEvidence only: it has no field through
which private datasets (inventory, suppliers, routes) could be passed.
"""
from __future__ import annotations

from enum import Enum
from typing import ClassVar, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from supply_chain.schemas import (
    INCIDENT_ID_PATTERN,
    PRODUCT_ID_PATTERN,
    ROUTE_ID_PATTERN,
    SITE_ID_PATTERN,
    SUPPLIER_ID_PATTERN,
    WAREHOUSE_ID_PATTERN,
    AgentStatus,
    DisruptionIncident,
    EvidenceStatus,
    ExecutionMode,
    IncidentAssessment,
    InventoryFindings,
    MitigationResult,
    Notification,
    RouteFindings,
    CostFindings,
    SupplierFindings,
)


class AgentName(str, Enum):
    MONITOR = "MonitorAgent"
    INVENTORY = "InventoryAgent"
    SUPPLIER = "SupplierAgent"
    ROUTE = "RouteAgent"
    COORDINATOR = "CoordinatorAgent"
    COST = "CostAgent"


REQUIRED_TOOLS: Dict[AgentName, tuple[str, str]] = {
    AgentName.MONITOR: ("validate_disruption", "identify_affected_sites"),
    AgentName.INVENTORY: ("get_inventory", "calculate_stock_shortage"),
    AgentName.SUPPLIER: ("find_alternative_suppliers", "check_supplier_capacity"),
    AgentName.ROUTE: ("get_available_routes", "calculate_route_delay"),
    AgentName.COORDINATOR: ("read_blackboard_evidence", "validate_mitigation_decision"),
    AgentName.COST: ("estimate_option_cost", "compare_options"),
}

BLACKBOARD_SECTION: Dict[AgentName, str] = {
    AgentName.MONITOR: "incident_assessment",
    AgentName.INVENTORY: "inventory_findings",
    AgentName.SUPPLIER: "supplier_findings",
    AgentName.ROUTE: "route_findings",
    AgentName.COORDINATOR: "mitigation_decision",
    AgentName.COST: "cost_findings",
}


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------

class AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    incident_id: str = Field(..., pattern=INCIDENT_ID_PATTERN)


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: ClassVar[AgentName]
    payload_field: ClassVar[str]

    incident_id: str = Field(..., pattern=INCIDENT_ID_PATTERN)
    status: AgentStatus
    execution_mode: ExecutionMode = ExecutionMode.DETERMINISTIC_FALLBACK
    tools_invoked: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    # Prose from the interpreter. Never used for calculations or decisions.
    interpretation: Optional[str] = None

    @property
    def agent_name(self) -> str:
        return self.agent.value

    @property
    def blackboard_section(self) -> str:
        return BLACKBOARD_SECTION[self.agent]

    @model_validator(mode="after")
    def _status_invariants(self) -> "AgentOutput":
        payload = getattr(self, self.payload_field)
        if self.status == AgentStatus.COMPLETE:
            if payload is None:
                raise ValueError(f"{self.agent.value}: COMPLETE output requires '{self.payload_field}'")
            if self.error:
                raise ValueError(f"{self.agent.value}: COMPLETE output cannot carry an error")
            missing = [t for t in REQUIRED_TOOLS[self.agent] if t not in self.tools_invoked]
            if missing:
                raise ValueError(f"{self.agent.value}: required tools not invoked: {missing}")
        elif self.status == AgentStatus.FAILED:
            if not self.error:
                raise ValueError(f"{self.agent.value}: FAILED output requires an error message")
            if payload is not None:
                raise ValueError(f"{self.agent.value}: FAILED output must not carry evidence")
        else:
            raise ValueError(f"{self.agent.value}: output status must be COMPLETE or FAILED")
        return self


# ---------------------------------------------------------------------------
# Agent 1: Monitor
# ---------------------------------------------------------------------------

class MonitorAgentInput(AgentInput):
    incident: DisruptionIncident

    @model_validator(mode="after")
    def _ids_match(self) -> "MonitorAgentInput":
        if self.incident.incident_id != self.incident_id:
            raise ValueError("incident_id does not match incident.incident_id")
        return self


class MonitorAgentOutput(AgentOutput):
    agent: ClassVar[AgentName] = AgentName.MONITOR
    payload_field: ClassVar[str] = "assessment"
    assessment: Optional[IncidentAssessment] = None


# ---------------------------------------------------------------------------
# Agent 2: Inventory
# ---------------------------------------------------------------------------

class InventoryAgentInput(AgentInput):
    product_id: str = Field(..., pattern=PRODUCT_ID_PATTERN)
    warehouse_id: str = Field(..., pattern=WAREHOUSE_ID_PATTERN)
    required_quantity: int = Field(..., gt=0)


class InventoryAgentOutput(AgentOutput):
    agent: ClassVar[AgentName] = AgentName.INVENTORY
    payload_field: ClassVar[str] = "findings"
    findings: Optional[InventoryFindings] = None


# ---------------------------------------------------------------------------
# Agent 3: Supplier
# Runs concurrently with Inventory, so it checks capacity against
# required_quantity and posts per-supplier capacity/lead time. The Coordinator
# joins this with inventory_findings.replenishment_required on the Blackboard.
# ---------------------------------------------------------------------------

class SupplierAgentInput(AgentInput):
    product_id: str = Field(..., pattern=PRODUCT_ID_PATTERN)
    warehouse_id: str = Field(..., pattern=WAREHOUSE_ID_PATTERN)
    required_quantity: int = Field(..., gt=0)
    deadline_days: int = Field(..., gt=0)
    excluded_supplier_ids: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids(self) -> "SupplierAgentInput":
        import re
        bad = [s for s in self.excluded_supplier_ids if not re.match(SUPPLIER_ID_PATTERN, s)]
        if bad:
            raise ValueError(f"invalid supplier ids: {bad}")
        return self


class SupplierAgentOutput(AgentOutput):
    agent: ClassVar[AgentName] = AgentName.SUPPLIER
    payload_field: ClassVar[str] = "findings"
    findings: Optional[SupplierFindings] = None


# ---------------------------------------------------------------------------
# Agent 4: Route
# ---------------------------------------------------------------------------

class RouteAgentInput(AgentInput):
    product_id: str = Field(..., pattern=PRODUCT_ID_PATTERN)
    affected_warehouse_id: str = Field(..., pattern=WAREHOUSE_ID_PATTERN)
    destination_id: str = Field(..., pattern=SITE_ID_PATTERN)
    required_quantity: int = Field(..., gt=0)  # needed to check stock at each route origin
    deadline_days: int = Field(..., gt=0)
    blocked_route_ids: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids(self) -> "RouteAgentInput":
        import re
        bad = [r for r in self.blocked_route_ids if not re.match(ROUTE_ID_PATTERN, r)]
        if bad:
            raise ValueError(f"invalid route ids: {bad}")
        return self


class RouteAgentOutput(AgentOutput):
    agent: ClassVar[AgentName] = AgentName.ROUTE
    payload_field: ClassVar[str] = "findings"
    findings: Optional[RouteFindings] = None


# ---------------------------------------------------------------------------
# Agent 5: Coordinator (Blackboard evidence ONLY)
# ---------------------------------------------------------------------------



class CostAgentInput(AgentInput):
    """Cost Agent input (reads from Blackboard)."""
    pass


class CostAgentOutput(AgentOutput):
    agent: ClassVar[AgentName] = AgentName.COST
    payload_field: ClassVar[str] = "findings"
    findings: Optional[CostFindings] = None


class BlackboardEvidence(BaseModel):
    """Read-only snapshot of Blackboard sections. Missing sections stay None."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    incident: Optional[DisruptionIncident] = None
    incident_assessment: Optional[IncidentAssessment] = None
    inventory_findings: Optional[InventoryFindings] = None
    supplier_findings: Optional[SupplierFindings] = None
    route_findings: Optional[RouteFindings] = None
    cost_findings: Optional[CostFindings] = None
    agent_status: Dict[str, AgentStatus] = Field(default_factory=dict)

    _SECTION_OWNER: ClassVar[Dict[str, AgentName]] = {
        "incident_assessment": AgentName.MONITOR,
        "inventory_findings": AgentName.INVENTORY,
        "supplier_findings": AgentName.SUPPLIER,
        "route_findings": AgentName.ROUTE,
        "cost_findings": AgentName.COST,
    }

    def evidence_status(self) -> Dict[str, EvidenceStatus]:
        """AVAILABLE if posted, FAILED if its agent failed, otherwise UNKNOWN."""
        result: Dict[str, EvidenceStatus] = {}
        for section, owner in self._SECTION_OWNER.items():
            if getattr(self, section) is not None:
                result[section] = EvidenceStatus.AVAILABLE
            elif self.agent_status.get(owner.value) == AgentStatus.FAILED:
                result[section] = EvidenceStatus.FAILED
            else:
                result[section] = EvidenceStatus.UNKNOWN
        return result


class CoordinatorAgentInput(AgentInput):
    evidence: BlackboardEvidence

    @model_validator(mode="after")
    def _ids_match(self) -> "CoordinatorAgentInput":
        inc = self.evidence.incident
        if inc is not None and inc.incident_id != self.incident_id:
            raise ValueError("evidence belongs to a different incident")
        return self


class CoordinatorAgentOutput(AgentOutput):
    agent: ClassVar[AgentName] = AgentName.COORDINATOR
    payload_field: ClassVar[str] = "decision"
    decision: Optional[MitigationResult] = None
    notifications: List[Notification] = Field(default_factory=list)
