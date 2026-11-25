import pytest
from pydantic import ValidationError

from supply_chain.contracts import (
    BLACKBOARD_SECTION,
    REQUIRED_TOOLS,
    AgentName,
    BlackboardEvidence,
    CoordinatorAgentInput,
    CoordinatorAgentOutput,
    InventoryAgentInput,
    InventoryAgentOutput,
    MonitorAgentInput,
    RouteAgentInput,
    SupplierAgentInput,
)
from supply_chain.schemas import AgentStatus, DisruptionIncident, EvidenceStatus, InventoryFindings

FINDINGS = InventoryFindings(
    warehouse_id="WH-PUN-01", product_id="LAPTOP-001", available_stock=25, safety_stock=40,
    required_quantity=100, shortage=115, safety_stock_met=False, recommendation="Reorder",
    usable_stock=0, replenishment_required=115,
)
INV_TOOLS = ["get_inventory", "calculate_stock_shortage"]


def test_six_agents_each_have_two_required_tools_and_one_section():
    assert len(AgentName) == 6
    assert set(REQUIRED_TOOLS) == set(AgentName) == set(BLACKBOARD_SECTION)
    assert all(len(set(t)) == 2 for t in REQUIRED_TOOLS.values())
    assert REQUIRED_TOOLS[AgentName.COORDINATOR] == ("read_blackboard_evidence", "validate_mitigation_decision")


def test_complete_output_requires_payload_and_both_tools():
    out = InventoryAgentOutput(incident_id="INC-1001", status="complete", tools_invoked=INV_TOOLS, findings=FINDINGS)
    assert out.agent_name == "InventoryAgent" and out.blackboard_section == "inventory_findings"
    with pytest.raises(ValidationError, match="required tools"):
        InventoryAgentOutput(incident_id="INC-1001", status="complete", tools_invoked=["get_inventory"], findings=FINDINGS)
    with pytest.raises(ValidationError, match="requires 'findings'"):
        InventoryAgentOutput(incident_id="INC-1001", status="complete", tools_invoked=INV_TOOLS)


def test_failed_output_needs_error_and_cannot_carry_evidence():
    failed = InventoryAgentOutput(incident_id="INC-1001", status="failed", error="dataset unavailable")
    assert failed.findings is None
    with pytest.raises(ValidationError, match="error message"):
        InventoryAgentOutput(incident_id="INC-1001", status="failed")
    with pytest.raises(ValidationError, match="must not carry evidence"):
        InventoryAgentOutput(incident_id="INC-1001", status="failed", error="x", findings=FINDINGS)


def test_output_status_must_be_terminal():
    with pytest.raises(ValidationError, match="COMPLETE or FAILED"):
        InventoryAgentOutput(incident_id="INC-1001", status="running", error="x")


def test_inputs_are_strict():
    with pytest.raises(ValidationError):
        InventoryAgentInput(incident_id="INC-1001", product_id="LAPTOP-001", warehouse_id="WH-PUN-01",
                            required_quantity=100, surprise="field")
    with pytest.raises(ValidationError):
        SupplierAgentInput(incident_id="INC-1001", product_id="LAPTOP-001", warehouse_id="WH-PUN-01",
                           required_quantity=100, deadline_days=7, excluded_supplier_ids=["ACME"])
    with pytest.raises(ValidationError):
        RouteAgentInput(incident_id="INC-1005", product_id="LAPTOP-001", affected_warehouse_id="WH-PUN-01",
                        destination_id="CUST-MUM", deadline_days=5, blocked_route_ids=["bad"])


def test_monitor_input_ids_must_match():
    inc = DisruptionIncident(incident_id="INC-1001", disruption_type="supplier_delay",
                             affected_product_id="LAPTOP-001", affected_warehouse_id="WH-PUN-01",
                             required_quantity=100, original_supplier_id="SUP-01")
    MonitorAgentInput(incident_id="INC-1001", incident=inc)
    with pytest.raises(ValidationError, match="does not match"):
        MonitorAgentInput(incident_id="INC-1002", incident=inc)


def test_coordinator_input_accepts_blackboard_evidence_only():
    with pytest.raises(ValidationError):
        CoordinatorAgentInput(incident_id="INC-1001", evidence=BlackboardEvidence(), inventory=[])
    with pytest.raises(ValidationError):
        BlackboardEvidence(suppliers=[])


def test_missing_evidence_is_unknown_and_failed_agent_is_failed_not_negative():
    ev = BlackboardEvidence(
        inventory_findings=FINDINGS,
        agent_status={"InventoryAgent": AgentStatus.COMPLETE, "SupplierAgent": AgentStatus.FAILED,
                      "RouteAgent": AgentStatus.RUNNING},
    )
    status = ev.evidence_status()
    assert status["inventory_findings"] is EvidenceStatus.AVAILABLE
    assert status["supplier_findings"] is EvidenceStatus.FAILED
    assert status["route_findings"] is EvidenceStatus.UNKNOWN
    assert status["incident_assessment"] is EvidenceStatus.UNKNOWN


def test_coordinator_failed_output_has_no_decision():
    out = CoordinatorAgentOutput(incident_id="INC-1001", status="failed", error="insufficient evidence")
    assert out.decision is None and out.notifications == []
