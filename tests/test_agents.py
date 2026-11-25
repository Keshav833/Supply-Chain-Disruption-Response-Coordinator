"""PHASE_5: five agents on the shared Blackboard — scenarios, INC-1004 fix and failure modes."""
import json

import pytest

import supply_chain.agents.coordinator_agent as coordinator_module
from supply_chain.agents import route_agent, supplier_agent
from supply_chain.agents.coordinator_agent import CoordinatorAgent
from supply_chain.agents.inventory_agent import InventoryAgent
from supply_chain.agents.monitor_agent import MonitorAgent
from supply_chain.agents.route_agent import RouteAgent
from supply_chain.blackboard import Blackboard
from supply_chain.contracts import BLACKBOARD_SECTION, REQUIRED_TOOLS, AgentName
from supply_chain.data.sample_data import SAMPLE_INCIDENTS, load_dataset
from supply_chain.schemas import (
    AgentStatus,
    DisruptionIncident,
    EvidenceStatus,
    ExecutionMode,
    IncidentAssessment,
    InventoryFindings,
    MitigationDecision,
)
from tests.helpers import run_pipeline

INCIDENT_IDS = [d["incident_id"] for d in SAMPLE_INCIDENTS]


def inc(inc_id, **changes):
    d = next(x for x in SAMPLE_INCIDENTS if x["incident_id"] == inc_id)
    return DisruptionIncident(**{**d, **changes})


def edit(path, mutate):
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


# --- Five scenarios ----------------------------------------------------------

@pytest.mark.parametrize("inc_id", INCIDENT_IDS)
async def test_scenario_outcomes(inc_id, expected_outcomes):
    res = await run_pipeline(inc(inc_id))
    exp = expected_outcomes[inc_id]
    d = res["coordinator"].decision
    assert d.decision.value == exp["expected_decision"]
    assert d.selected_option_id == exp["expected_option_id"]
    assert d.authorized is False and d.execution_mode == ExecutionMode.DETERMINISTIC_FALLBACK
    bb = res["blackboard"]
    assert all(s == AgentStatus.COMPLETE for s in bb.agent_status.values())
    assert set(bb.evidence_status().values()) == {EvidenceStatus.AVAILABLE}
    assert bb.mitigation_decision == d and bb.notifications


@pytest.mark.parametrize("inc_id", INCIDENT_IDS)
async def test_every_agent_invokes_both_tools_and_shares_one_blackboard(inc_id):
    res = await run_pipeline(inc(inc_id))
    bb = res["blackboard"]
    for agent, out in res["outputs"].items():
        assert out.status == AgentStatus.COMPLETE
        assert set(REQUIRED_TOOLS[agent]) <= set(out.tools_invoked) == set(bb.tools_invoked[agent.value])
        assert getattr(bb, BLACKBOARD_SECTION[agent]) == getattr(out, out.payload_field)
        assert out.interpretation.startswith("[deterministic_fallback]")
        logged = {e.action for e in bb.run_log if e.agent_name == agent.value}
        assert {f"tool_call:{t}" for t in REQUIRED_TOOLS[agent]} <= logged
    assert {e.execution_id for e in bb.run_log} == {bb.execution_id}


async def test_specialists_run_concurrently_on_the_shared_blackboard(monkeypatch):
    import asyncio
    from supply_chain.agents.supplier_agent import SupplierAgent
    for cls in (InventoryAgent, SupplierAgent, RouteAgent):
        original = cls.execute

        async def slow(self, kernel, bb, _orig=original):
            await asyncio.sleep(0.01)  # a sequential pipeline would finish one before starting the next
            return await _orig(self, kernel, bb)
        monkeypatch.setattr(cls, "execute", slow)
    bb = (await run_pipeline(inc("INC-1001")))["blackboard"]
    order = [e.agent_name for e in bb.run_log if e.action == "agent_started"]
    assert order[0] == "MonitorAgent" and order[-1] == "CoordinatorAgent"
    first_publish = min(i for i, e in enumerate(bb.run_log)
                        if e.action == "evidence_published" and e.agent_name != "MonitorAgent")
    starts = [i for i, e in enumerate(bb.run_log) if e.action == "agent_started"
              and e.agent_name in ("InventoryAgent", "SupplierAgent", "RouteAgent")]
    assert max(starts) < first_publish  # all three started before any of them finished


# --- INC-1004 root cause: route origin must hold the stock --------------------

async def test_inc_1004_routes_from_understocked_warehouses_are_not_viable(data_copy):
    """General rule, no incident-specific code: add a fast route into WH-DEL-03 from a warehouse
    with no usable LAPTOP-001; the decision must stay HOLD."""
    edit(data_copy / "routes.json", lambda r: r.append(
        {"route_id": "RT-PUN-DEL-07", "origin": "WH-PUN-01", "destination": "WH-DEL-03", "is_available": True,
         "normal_transit_days": 1, "current_delay_days": 0, "availability_score": 0.99}))
    ds = load_dataset(data_copy)
    res = await run_pipeline(inc("INC-1004"), dataset=ds)
    rf = res["blackboard"].route_findings
    assert [(a.route_id, a.origin_usable_stock, a.stock_sufficient, a.within_deadline, a.viable)
            for a in rf.route_assessments] == [("RT-PUN-DEL-07", 0, False, True, False)]
    assert rf.best_route is None
    assert res["coordinator"].decision.decision == MitigationDecision.HOLD

    # Positive control: same route, origin now has enough usable stock -> REROUTE.
    edit(data_copy / "inventory.json", lambda inv: [r.update(available_stock=400) for r in inv
                                                    if (r["warehouse_id"], r["product_id"]) ==
                                                    ("WH-PUN-01", "LAPTOP-001")])
    res2 = await run_pipeline(inc("INC-1004"), dataset=load_dataset(data_copy))
    assert res2["coordinator"].decision.decision == MitigationDecision.REROUTE
    assert res2["coordinator"].decision.selected_option_id == "RT-PUN-DEL-07"


async def test_fast_route_from_affected_warehouse_without_stock_is_rejected():
    """The old prototype picked routes out of the affected warehouse regardless of its stock."""
    res = await run_pipeline(inc("INC-1004", destination_id="CUST-DEL"))
    rf = res["blackboard"].route_findings
    a = {x.route_id: x for x in rf.route_assessments}
    assert a["RT-DEL-DEL-05"].within_deadline and not a["RT-DEL-DEL-05"].stock_sufficient
    assert a["RT-DEL-DEL-05"].origin_usable_stock == 60 < 240
    assert res["coordinator"].decision.decision == MitigationDecision.HOLD


async def test_inc_1001_route_with_insufficient_origin_stock_is_rejected():
    rf = (await run_pipeline(inc("INC-1001")))["blackboard"].route_findings
    (a,) = rf.route_assessments
    assert (a.route_id, a.origin_usable_stock, a.viable) == ("RT-MUM-PUN-06", 90, False)


# --- Failure modes -------------------------------------------------------------

async def test_tool_failure_publishes_failed_status_and_no_evidence(monkeypatch):
    def broken(*a, **kw):
        raise ConnectionError("route service unreachable")
    monkeypatch.setattr(route_agent, "calculate_route_delay", broken)
    res = await run_pipeline(inc("INC-1005"))
    bb, out = res["blackboard"], res["outputs"][AgentName.ROUTE]
    assert out.status == AgentStatus.FAILED and out.findings is None
    assert "ConnectionError: route service unreachable" in out.error
    assert out.tools_invoked == ["get_available_routes"]  # failed call not counted
    assert bb.route_findings is None and bb.agent_status["RouteAgent"] == AgentStatus.FAILED
    assert bb.evidence_status()["route_findings"] == EvidenceStatus.FAILED
    failed_call = [e for e in bb.run_log if e.action == "tool_call:calculate_route_delay"]
    assert failed_call and "ConnectionError" in failed_call[0].error
    d = res["coordinator"].decision
    assert d.decision == MitigationDecision.UNKNOWN and d.selected_option_id is None
    assert d.evidence_summary["failed_sections"] == ["route_findings"]


async def test_agent_failure_on_missing_inventory_record_does_not_assume_zero(data_copy):
    edit(data_copy / "inventory.json", lambda inv: inv.remove(next(
        r for r in inv if (r["warehouse_id"], r["product_id"]) == ("WH-PUN-01", "LAPTOP-001"))))
    res = await run_pipeline(inc("INC-1001"), dataset=load_dataset(data_copy))
    out = res["outputs"][AgentName.INVENTORY]
    assert out.status == AgentStatus.FAILED and out.findings is None and "UNKNOWN, not zero" in out.error
    assert res["blackboard"].inventory_findings is None
    assert res["coordinator"].decision.decision == MitigationDecision.UNKNOWN


async def test_invalid_tool_output_is_treated_as_failure(monkeypatch):
    monkeypatch.setattr(supplier_agent, "find_alternative_suppliers", lambda *a, **k: {"ok": True})
    res = await run_pipeline(inc("INC-1001"))
    out = res["outputs"][AgentName.SUPPLIER]
    assert out.status == AgentStatus.FAILED and "expected a validated model" in out.error
    assert res["coordinator"].decision.decision == MitigationDecision.UNKNOWN


async def test_missing_evidence_stays_unknown_and_blocks_decision():
    res = await run_pipeline(inc("INC-1001"), skip=(AgentName.SUPPLIER,))
    d = res["coordinator"].decision
    assert d.decision == MitigationDecision.UNKNOWN and d.selected_option_id is None
    assert d.evidence_summary["evidence_status"]["supplier_findings"] == "UNKNOWN"
    assert d.evidence_summary["missing_sections"] == ["supplier_findings"]
    assert res["blackboard"].agent_status["SupplierAgent"] == AgentStatus.PENDING


async def test_coordinator_on_empty_blackboard_returns_unknown_not_a_guess():
    bb = Blackboard(inc("INC-1002"))
    out = await CoordinatorAgent().run(bb)
    assert out.status == AgentStatus.COMPLETE
    assert out.decision.decision == MitigationDecision.UNKNOWN
    assert len(out.decision.evidence_summary["missing_sections"]) == 4
    assert set(out.tools_invoked) == set(REQUIRED_TOOLS[AgentName.COORDINATOR])


async def test_coordinator_proposal_failing_validation_publishes_failure(monkeypatch):
    monkeypatch.setattr(coordinator_module, "propose_decision",
                        lambda report: (MitigationDecision.REROUTE, "RT-MUM-PUN-06", "forced bad proposal"))
    res = await run_pipeline(inc("INC-1001"))
    out = res["coordinator"]
    assert out.status == AgentStatus.FAILED and out.decision is None and "failed validation" in out.error
    assert res["blackboard"].mitigation_decision is None
    assert res["blackboard"].agent_status["CoordinatorAgent"] == AgentStatus.FAILED


async def _board_with(incident, inventory_override=None):
    """Run Monitor/Supplier/Route normally; publish a hand-built inventory section."""
    bb = Blackboard(incident)
    await MonitorAgent().run(bb)
    from supply_chain.agents.supplier_agent import SupplierAgent
    await SupplierAgent().run(bb)
    await RouteAgent().run(bb)
    await bb.mark_running(AgentName.INVENTORY)
    await bb.publish(AgentName.INVENTORY, "inventory_findings", inventory_override)
    return bb


async def test_contradictory_evidence_blocks_decision():
    # Inventory claims WH-MUM-02 has 5 usable units, the route evidence says 90.
    i = inc("INC-1005")
    findings = InventoryFindings(
        warehouse_id="WH-PUN-01", product_id="LAPTOP-001", available_stock=25, safety_stock=40,
        required_quantity=30, shortage=45, safety_stock_met=False, recommendation="x", usable_stock=0,
        replenishment_required=45,
        alternative_stock_locations=[{"warehouse_id": "WH-MUM-02", "available_stock": 35,
                                      "safety_stock": 30, "usable_stock": 5}])
    bb = await _board_with(i, findings)
    out = await CoordinatorAgent().run(bb)
    assert out.decision.decision == MitigationDecision.UNKNOWN
    assert any("contradicts inventory evidence" in c for c in out.decision.evidence_summary["contradictions"])


async def test_invalid_inventory_arithmetic_blocks_decision():
    i = inc("INC-1001")
    findings = InventoryFindings(
        warehouse_id="WH-PUN-01", product_id="LAPTOP-001", available_stock=25, safety_stock=40,
        required_quantity=100, shortage=115, safety_stock_met=False, recommendation="x", usable_stock=0,
        replenishment_required=10)  # should be 115
    bb = await _board_with(i, findings)
    out = await CoordinatorAgent().run(bb)
    assert out.decision.decision == MitigationDecision.UNKNOWN
    assert "inventory replenishment_required is inconsistent" in out.decision.evidence_summary["contradictions"]


async def test_invalid_incident_is_not_investigated(data_copy):
    # Network data says the "blocked" route is open: the report contradicts master data.
    edit(data_copy / "routes.json", lambda r: r[0].update(is_available=True, availability_score=0.9))
    edit(data_copy / "incidents.json", lambda d: d.pop(4))  # keep the copied dataset loadable
    res = await run_pipeline(inc("INC-1005"), dataset=load_dataset(data_copy))
    bb = res["blackboard"]
    assert bb.incident_assessment.is_valid is False and bb.incident_assessment.severity == "UNKNOWN"
    for a in ("InventoryAgent", "SupplierAgent", "RouteAgent"):
        assert bb.agent_status[a] == AgentStatus.FAILED and "failed validation" in bb.errors[a]
    assert res["coordinator"].decision.decision == MitigationDecision.UNKNOWN


async def test_monitor_failure_publishes_failed_assessment(monkeypatch):
    import supply_chain.agents.monitor_agent as m
    monkeypatch.setattr(m, "identify_affected_sites", lambda *a, **k: (_ for _ in ()).throw(KeyError("x")))
    res = await run_pipeline(inc("INC-1001"))
    bb = res["blackboard"]
    assert bb.incident_assessment is None
    assert bb.evidence_status()["incident_assessment"] == EvidenceStatus.FAILED
    assert res["coordinator"].decision.decision == MitigationDecision.UNKNOWN


def test_coordinator_has_no_access_to_raw_datasets():
    src = open(coordinator_module.__file__, encoding="utf-8").read()
    for forbidden in ("sample_data", "DATASET", "load_dataset", "SupplyChainDataset", "inventory.json",
                      "dataset="):
        assert forbidden not in src
    import inspect
    assert "dataset" not in inspect.signature(CoordinatorAgent.__init__).parameters
    from supply_chain.tools import coordinator_tools
    tsrc = open(coordinator_tools.__file__, encoding="utf-8").read()
    assert "sample_data" not in tsrc and "load_dataset" not in tsrc


async def test_run_log_persists_full_trace(tmp_path):
    bb = (await run_pipeline(inc("INC-1002")))["blackboard"]
    path = tmp_path / "incident_runs.jsonl"
    n = bb.persist_run_log(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert n == len(lines) == len(bb.run_log)
    assert json.loads(lines[-1])["action"] == "evidence_published"
    assert json.loads(lines[-1])["agent_name"] == "CoordinatorAgent"
