"""PHASE_3: Blackboard ownership, evidence semantics, concurrency and JSONL logging."""
import asyncio
import json

import pytest

from supply_chain.blackboard import (
    Blackboard,
    BlackboardStateError,
    OwnershipError,
    SECTION_OWNER,
)
from supply_chain.contracts import BLACKBOARD_SECTION, AgentName
from supply_chain.schemas import (
    AgentLogEntry,
    AgentStatus,
    CostFindings,
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

INC = DisruptionIncident(incident_id="INC-1001", disruption_type="supplier_delay",
                         affected_product_id="LAPTOP-001", affected_warehouse_id="WH-PUN-01",
                         required_quantity=100, original_supplier_id="SUP-01", deadline_days=7)


def assessment(incident_id="INC-1001"):
    return IncidentAssessment(incident_id=incident_id, is_valid=True, validation_message="ok",
                              affected_sites=["WH-PUN-01"], severity="HIGH", requires_immediate_action=False)


def inventory():
    return InventoryFindings(warehouse_id="WH-PUN-01", product_id="LAPTOP-001", available_stock=25,
                             safety_stock=40, required_quantity=100, shortage=115, safety_stock_met=False,
                             recommendation="reorder", usable_stock=0, replenishment_required=115)


def supplier():
    return SupplierFindings(viable_suppliers=[], best_supplier=None, can_fulfill_by_deadline=False,
                            recommendation="none", excluded_supplier_ids=["SUP-01"])


def route():
    return RouteFindings(available_routes=[], best_route=None, can_deliver_by_deadline=False,
                         recommendation="none", destination_id="WH-PUN-01")


def decision():
    return MitigationResult(incident_id="INC-1001", decision="HOLD", justification="x",
                            parties_to_notify=[], evidence_summary={})


def cost():
    return CostFindings(incident_id="INC-1001", cost_estimates=[], cheapest_option=None,
                        cost_comparison={}, recommendation="none")


PAYLOADS = {"incident_assessment": assessment, "inventory_findings": inventory,
            "supplier_findings": supplier, "route_findings": route,
            "cost_findings": cost, "mitigation_decision": decision}


async def running(bb, *agents):
    for a in agents:
        await bb.mark_running(a)


def test_section_ownership_map_matches_contracts():
    assert SECTION_OWNER == {s: a for a, s in BLACKBOARD_SECTION.items()}
    assert len(SECTION_OWNER) == 6


async def test_each_agent_can_write_only_its_own_section():
    for writer in AgentName:
        for section, owner in SECTION_OWNER.items():
            bb = Blackboard(INC)
            await bb.mark_running(writer)
            if owner == writer:
                await bb.publish(writer, section, PAYLOADS[section]())
                assert bb.get_section(section) is not None
                assert bb.agent_status[writer.value] == AgentStatus.COMPLETE
            else:
                with pytest.raises(OwnershipError):
                    await bb.publish(writer, section, PAYLOADS[section]())
                assert bb.get_section(section) is None


async def test_incident_and_unknown_sections_are_not_writable():
    bb = Blackboard(INC)
    await running(bb, AgentName.MONITOR)
    for section in ("incident", "agent_status", "bogus"):
        with pytest.raises(OwnershipError):
            await bb.publish(AgentName.MONITOR, section, assessment())


async def test_only_coordinator_publishes_notifications():
    bb = Blackboard(INC)
    await running(bb, AgentName.INVENTORY, AgentName.COORDINATOR)
    note = [Notification(party="Ops", reason="r")]
    with pytest.raises(OwnershipError):
        await bb.publish(AgentName.INVENTORY, "inventory_findings", inventory(), notifications=note)
    await bb.publish(AgentName.COORDINATOR, "mitigation_decision", decision(), notifications=note)
    assert [n.party for n in bb.notifications] == ["Ops"]


async def test_publish_requires_running_type_match_same_incident_and_is_write_once():
    bb = Blackboard(INC)
    with pytest.raises(BlackboardStateError, match="RUNNING"):
        await bb.publish(AgentName.MONITOR, "incident_assessment", assessment())
    await bb.mark_running(AgentName.MONITOR)
    with pytest.raises(TypeError):
        await bb.publish(AgentName.MONITOR, "incident_assessment", inventory())
    with pytest.raises(BlackboardStateError, match="INC-1002"):
        await bb.publish(AgentName.MONITOR, "incident_assessment", assessment("INC-1002"))
    await bb.publish(AgentName.MONITOR, "incident_assessment", assessment())
    with pytest.raises(BlackboardStateError):
        await bb.publish(AgentName.MONITOR, "incident_assessment", assessment())
    with pytest.raises(BlackboardStateError):
        await bb.mark_running(AgentName.MONITOR)


async def test_published_payload_is_detached_from_caller_object():
    bb = Blackboard(INC)
    await running(bb, AgentName.MONITOR)
    a = assessment()
    await bb.publish(AgentName.MONITOR, "incident_assessment", a)
    a.affected_sites.append("WH-HACK-99")
    assert bb.incident_assessment.affected_sites == ["WH-PUN-01"]


async def test_missing_evidence_stays_unknown_and_failure_is_failed_not_negative():
    bb = Blackboard(INC)
    assert set(bb.evidence_status().values()) == {EvidenceStatus.UNKNOWN}
    await running(bb, AgentName.MONITOR, AgentName.ROUTE)
    await bb.publish(AgentName.MONITOR, "incident_assessment", assessment())
    await bb.mark_failed(AgentName.ROUTE, "route service down")
    status = bb.evidence_status()
    assert status == {"incident_assessment": EvidenceStatus.AVAILABLE,
                      "inventory_findings": EvidenceStatus.UNKNOWN,
                      "supplier_findings": EvidenceStatus.UNKNOWN,
                      "route_findings": EvidenceStatus.FAILED,
                      "cost_findings": EvidenceStatus.UNKNOWN}
    assert bb.route_findings is None  # no fabricated evidence
    assert bb.errors == {"RouteAgent": "route service down"}
    snap = bb.snapshot_evidence()
    assert snap.inventory_findings is None and snap.route_findings is None


async def test_failure_rules():
    bb = Blackboard(INC)
    with pytest.raises(ValueError):
        await bb.mark_failed(AgentName.ROUTE, "  ")
    await running(bb, AgentName.MONITOR)
    await bb.publish(AgentName.MONITOR, "incident_assessment", assessment())
    with pytest.raises(BlackboardStateError):
        await bb.mark_failed(AgentName.MONITOR, "late failure cannot erase published evidence")


async def test_snapshot_contains_only_published_evidence_and_is_immutable():
    bb = Blackboard(INC)
    await running(bb, AgentName.MONITOR, AgentName.COORDINATOR)
    await bb.publish(AgentName.MONITOR, "incident_assessment", assessment())
    snap = bb.snapshot_evidence()
    assert set(type(snap).model_fields) == {"incident", "incident_assessment", "inventory_findings",
                                            "supplier_findings", "route_findings", "cost_findings",
                                            "agent_status"}
    with pytest.raises(Exception):
        snap.inventory_findings = inventory()
    snap.incident_assessment.affected_sites.append("X")
    assert bb.incident_assessment.affected_sites == ["WH-PUN-01"]


async def test_concurrent_updates_do_not_corrupt_state():
    bb = Blackboard(INC)
    await running(bb, AgentName.MONITOR)
    await bb.publish(AgentName.MONITOR, "incident_assessment", assessment())
    await running(bb, AgentName.INVENTORY, AgentName.SUPPLIER, AgentName.ROUTE, AgentName.COST)

    async def work(agent, section, payload):
        for i in range(25):
            await bb.record_tool_call(agent, f"tool_{i}", ok=True)
            await asyncio.sleep(0)
        await bb.publish(agent, section, payload)

    await asyncio.gather(work(AgentName.INVENTORY, "inventory_findings", inventory()),
                         work(AgentName.SUPPLIER, "supplier_findings", supplier()),
                         work(AgentName.ROUTE, "route_findings", route()),
                         work(AgentName.COST, "cost_findings", cost()))
    assert all(len(bb.tools_invoked[a.value]) == 25 for a in
               (AgentName.INVENTORY, AgentName.SUPPLIER, AgentName.ROUTE, AgentName.COST))
    assert set(bb.evidence_status().values()) == {EvidenceStatus.AVAILABLE}
    assert [e.sequence for e in bb.run_log] == list(range(len(bb.run_log)))


async def test_concurrent_duplicate_writes_only_one_wins():
    bb = Blackboard(INC)
    await running(bb, AgentName.ROUTE)
    results = await asyncio.gather(*(bb.publish(AgentName.ROUTE, "route_findings", route()) for _ in range(10)),
                                   return_exceptions=True)
    assert sum(r is None for r in results) == 1
    assert all(isinstance(r, BlackboardStateError) for r in results if r is not None)


async def test_log_records_status_tools_errors_and_decisions():
    bb = Blackboard(INC, execution_id="exec-1")
    await running(bb, AgentName.ROUTE, AgentName.COORDINATOR)
    await bb.record_tool_call(AgentName.ROUTE, "get_available_routes", ok=False, error="boom")
    await bb.mark_failed(AgentName.ROUTE, "boom")
    await bb.publish(AgentName.COORDINATOR, "mitigation_decision", decision())
    actions = [(e.agent_name, e.action, e.status) for e in bb.run_log]
    assert actions == [("Orchestrator", "incident_received", AgentStatus.COMPLETE),
                       ("RouteAgent", "agent_started", AgentStatus.RUNNING),
                       ("CoordinatorAgent", "agent_started", AgentStatus.RUNNING),
                       ("RouteAgent", "tool_call:get_available_routes", AgentStatus.RUNNING),
                       ("RouteAgent", "agent_failed", AgentStatus.FAILED),
                       ("CoordinatorAgent", "evidence_published", AgentStatus.COMPLETE)]
    assert bb.tools_invoked["RouteAgent"] == []  # failed calls are not counted as invoked
    assert bb.run_log[-1].execution_mode == ExecutionMode.DETERMINISTIC_FALLBACK
    assert {e.execution_id for e in bb.run_log} == {"exec-1"}
    assert {e.incident_id for e in bb.run_log} == {"INC-1001"}


async def test_jsonl_persistence_is_append_only_without_duplicates(tmp_path):
    path = tmp_path / "logs" / "runs.jsonl"
    path.parent.mkdir()
    path.write_text('{"pre": "existing line"}\n', encoding="utf-8")
    bb = Blackboard(INC)
    await running(bb, AgentName.MONITOR)
    first = bb.persist_run_log(path)
    assert first == 2
    assert bb.persist_run_log(path) == 0  # nothing new: no duplicates
    await bb.publish(AgentName.MONITOR, "incident_assessment", assessment())
    assert bb.persist_run_log(path) == 1
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"pre": "existing line"}'
    entries = [AgentLogEntry.model_validate_json(line) for line in lines[1:]]
    assert [e.sequence for e in entries] == [0, 1, 2]
    other = Blackboard(INC)
    other.persist_run_log(path)
    lines2 = path.read_text(encoding="utf-8").splitlines()
    assert lines2[:4] == lines and len(lines2) == 5
    assert json.loads(lines2[4])["execution_id"] == other.execution_id != bb.execution_id


def test_blackboard_requires_validated_incident():
    with pytest.raises(TypeError):
        Blackboard({"incident_id": "INC-1001"})
