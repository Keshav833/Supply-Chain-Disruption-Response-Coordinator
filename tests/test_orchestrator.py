"""PHASE_6: integration tests of the PRODUCTION orchestrator (supply_chain.orchestrator)."""
import asyncio
import json
import time

import pytest

from supply_chain.agents import route_agent
from supply_chain.agents.coordinator_agent import CoordinatorAgent
from supply_chain.agents.inventory_agent import InventoryAgent
from supply_chain.agents.route_agent import RouteAgent
from supply_chain.agents.supplier_agent import SupplierAgent
from supply_chain.contracts import REQUIRED_TOOLS, AgentName
from supply_chain.data.sample_data import SAMPLE_INCIDENTS, load_dataset
from supply_chain.orchestrator import IncidentResponse, SupplyChainOrchestrator, run_incident
from supply_chain.schemas import AgentLogEntry, AgentStatus, ExecutionMode, MitigationDecision

INCIDENT_IDS = [d["incident_id"] for d in SAMPLE_INCIDENTS]


def raw(inc_id, **changes):
    return {**next(d for d in SAMPLE_INCIDENTS if d["incident_id"] == inc_id), **changes}


def edit(path, mutate):
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def read_log(path):
    return [AgentLogEntry.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / "logs" / "incident_runs.jsonl"


# --- Five scenarios ------------------------------------------------------------

@pytest.mark.parametrize("inc_id", INCIDENT_IDS)
async def test_scenarios_through_production_orchestrator(inc_id, expected_outcomes, log_path):
    r = await run_incident(raw(inc_id), log_path=log_path)
    exp = expected_outcomes[inc_id]
    assert isinstance(r, IncidentResponse)
    assert (r.outcome, r.decision.value, r.selected_option_id) == \
        ("decided", exp["expected_decision"], exp["expected_option_id"])
    assert r.authorized is False and r.llm_used is False and r.errors == []
    assert set(r.agent_execution_modes.values()) == {ExecutionMode.DETERMINISTIC_FALLBACK}
    assert all(s == AgentStatus.COMPLETE for s in r.agent_status.values())
    assert set(r.evidence_status.values()) == {"AVAILABLE"}
    assert r.evidence["mitigation_decision"]["decision"] == exp["expected_decision"]
    assert r.final_validation["is_valid"] is True
    assert r.justification and r.interpretation.startswith("[deterministic_fallback]")
    for agent, tools in REQUIRED_TOOLS.items():
        assert set(tools) <= set(r.tools_invoked[agent.value])
    actions = [e["action"] for e in r.trace]
    assert actions[0] == "incident_received" and actions[-1] == "run_completed"
    assert "final_decision_validated" in actions
    assert r.log_persisted and r.log_entries_written == len(r.trace) == len(read_log(log_path))


# --- Invalid incidents ------------------------------------------------------------

async def test_schema_invalid_incident_is_rejected_safely(log_path):
    r = await run_incident({"incident_id": "INC-9999", "disruption_type": "earthquake"}, log_path=log_path)
    assert r.outcome == "rejected" and r.decision == MitigationDecision.UNKNOWN
    assert r.selected_option_id is None and r.evidence == {} and r.agent_status == {}
    assert "schema validation" in r.justification and "disruption_type" in r.justification
    (entry,) = read_log(log_path)
    assert entry.action == "incident_rejected" and entry.execution_id == r.run_id and entry.incident_id == "INC-9999"


async def test_referentially_invalid_incident_stops_after_monitor(log_path):
    r = await run_incident(raw("INC-1005", original_route_id="RT-MUM-MUM-02"), log_path=log_path)
    assert r.outcome == "rejected" and r.decision == MitigationDecision.UNKNOWN
    assert r.agent_status["MonitorAgent"] == AgentStatus.COMPLETE
    assert r.evidence["incident_assessment"]["is_valid"] is False
    for a in ("InventoryAgent", "SupplierAgent", "RouteAgent", "CoordinatorAgent"):
        assert r.agent_status[a] == AgentStatus.PENDING  # never started
        assert r.tools_invoked[a] == []
    assert r.evidence["mitigation_decision"] is None and r.notifications == []
    assert "workflow_stopped" in [e["action"] for e in r.trace]
    assert r.log_persisted


async def test_monitor_failure_stops_workflow(monkeypatch, log_path):
    import supply_chain.agents.monitor_agent as m

    def broken(*a, **k):
        raise RuntimeError("master data unavailable")
    monkeypatch.setattr(m, "validate_disruption", broken)
    r = await run_incident(raw("INC-1001"), log_path=log_path)
    assert r.outcome == "failed" and r.decision == MitigationDecision.UNKNOWN
    assert r.agent_status["MonitorAgent"] == AgentStatus.FAILED
    assert "master data unavailable" in r.agent_errors["MonitorAgent"]
    assert r.evidence_status["incident_assessment"] == "FAILED"
    assert r.agent_status["CoordinatorAgent"] == AgentStatus.PENDING


# --- Agent / tool failure ---------------------------------------------------------

async def test_tool_failure_is_recorded_and_other_evidence_preserved(monkeypatch, log_path):
    def broken(*a, **k):
        raise ConnectionError("route service unreachable")
    monkeypatch.setattr(route_agent, "get_available_routes", broken)
    r = await run_incident(raw("INC-1002"), log_path=log_path)
    assert r.outcome == "undetermined" and r.decision == MitigationDecision.UNKNOWN
    assert r.agent_status["RouteAgent"] == AgentStatus.FAILED
    assert "ConnectionError: route service unreachable" in r.agent_errors["RouteAgent"]
    assert r.evidence["route_findings"] is None and r.evidence_status["route_findings"] == "FAILED"
    # Remaining valid evidence is kept, not discarded or overwritten.
    assert r.evidence["inventory_findings"]["warehouse_id"] == "WH-BLR-04"
    assert r.evidence["supplier_findings"] is not None
    assert r.agent_status["CoordinatorAgent"] == AgentStatus.COMPLETE
    assert r.evidence["mitigation_decision"]["evidence_summary"]["failed_sections"] == ["route_findings"]
    assert any(e.action == "tool_call:get_available_routes" and "ConnectionError" in (e.error or "")
               for e in read_log(log_path))
    assert r.agent_errors["RouteAgent"] in r.errors


async def test_agent_failure_missing_inventory_is_not_zero_stock(data_copy, log_path):
    edit(data_copy / "inventory.json", lambda inv: inv.remove(next(
        x for x in inv if (x["warehouse_id"], x["product_id"]) == ("WH-PUN-01", "LAPTOP-001"))))
    r = await run_incident(raw("INC-1001"), dataset=load_dataset(data_copy), log_path=log_path)
    assert r.agent_status["InventoryAgent"] == AgentStatus.FAILED
    assert "UNKNOWN, not zero" in r.agent_errors["InventoryAgent"]
    assert r.evidence["inventory_findings"] is None
    assert r.decision == MitigationDecision.UNKNOWN  # SUP-02 is feasible, but no REORDER without inventory


async def test_agent_timeout_is_recorded_as_failure(monkeypatch, log_path):
    async def hang(self, kernel, bb):
        await asyncio.sleep(5)
    monkeypatch.setattr(SupplierAgent, "execute", hang)
    r = await run_incident(raw("INC-1001"), log_path=log_path, agent_timeout=0.2)
    assert r.agent_status["SupplierAgent"] == AgentStatus.FAILED
    assert "timed out" in r.agent_errors["SupplierAgent"]
    assert r.evidence_status["inventory_findings"] == "AVAILABLE"
    assert r.decision == MitigationDecision.UNKNOWN


async def test_coordinator_failure_returns_failed_unknown(monkeypatch, log_path):
    import supply_chain.agents.coordinator_agent as c
    monkeypatch.setattr(c, "propose_decision", lambda rep: (MitigationDecision.REORDER, "SUP-04", "bad"))
    r = await run_incident(raw("INC-1001"), log_path=log_path)
    assert r.outcome == "failed" and r.decision == MitigationDecision.UNKNOWN and r.selected_option_id is None
    assert r.agent_status["CoordinatorAgent"] == AgentStatus.FAILED
    assert r.evidence["mitigation_decision"] is None


async def test_final_validation_gate_rejects_unsafe_published_decision(monkeypatch, log_path):
    """Even if a (buggy) coordinator publishes an unsafe decision, the orchestrator's gate rejects it."""
    import supply_chain.agents.coordinator_agent as c
    from supply_chain.tools.coordinator_tools import DecisionValidation
    monkeypatch.setattr(c, "propose_decision", lambda rep: (MitigationDecision.REROUTE, "RT-MUM-PUN-06", "bad"))
    original = CoordinatorAgent.call_tool

    async def lenient(self, kernel, tool, model, **kw):
        result = await original(self, kernel, tool, model, **kw)
        if tool == "validate_mitigation_decision":  # simulate a broken internal check
            return DecisionValidation(decision=result.decision, option_id=result.option_id, is_valid=True,
                                      violations=[])
        return result
    monkeypatch.setattr(CoordinatorAgent, "call_tool", lenient)
    r = await run_incident(raw("INC-1001"), log_path=log_path)
    assert r.outcome == "failed" and r.decision == MitigationDecision.UNKNOWN
    assert any("90 usable units < required 100" in v for v in r.final_validation["violations"])
    assert "final_decision_rejected" in [e["action"] for e in r.trace]


# --- Missing and contradictory evidence ----------------------------------------------

async def test_missing_evidence_gives_unknown(log_path):
    orch = SupplyChainOrchestrator(log_path=log_path, specialists=[InventoryAgent(), RouteAgent()])
    r = await orch.run(raw("INC-1001"))
    assert r.outcome == "undetermined" and r.decision == MitigationDecision.UNKNOWN
    assert r.evidence_status["supplier_findings"] == "UNKNOWN"
    assert r.agent_status["SupplierAgent"] == AgentStatus.PENDING
    assert r.final_validation["is_valid"] is True  # UNKNOWN is the only valid answer here


async def test_contradictory_evidence_gives_unknown(data_copy, log_path):
    # The inventory data source is stale: WH-MUM-02 shows 5 usable units, the route data source 90.
    edit(data_copy / "inventory.json", lambda inv: [x.update(available_stock=35) for x in inv
                                                    if (x["warehouse_id"], x["product_id"]) ==
                                                    ("WH-MUM-02", "LAPTOP-001")])
    stale = load_dataset(data_copy)
    orch = SupplyChainOrchestrator(log_path=log_path,
                                   specialists=[InventoryAgent(dataset=stale), SupplierAgent(), RouteAgent()])
    r = await orch.run(raw("INC-1005"))
    assert r.decision == MitigationDecision.UNKNOWN and r.outcome == "undetermined"
    contradictions = r.evidence["mitigation_decision"]["evidence_summary"]["contradictions"]
    assert any("RT-MUM-MUM-02: origin stock 90 contradicts inventory evidence 5" in c for c in contradictions)


# --- Concurrency -----------------------------------------------------------------------

async def test_investigation_agents_run_concurrently_and_coordinator_waits(monkeypatch, log_path):
    for cls in (InventoryAgent, SupplierAgent, RouteAgent):
        original = cls.execute

        async def slow(self, kernel, bb, _orig=original):
            await asyncio.sleep(0.3)
            return await _orig(self, kernel, bb)
        monkeypatch.setattr(cls, "execute", slow)
    t0 = time.perf_counter()
    r = await run_incident(raw("INC-1001"), log_path=log_path)
    elapsed = time.perf_counter() - t0
    assert r.decision == MitigationDecision.REORDER
    assert elapsed < 0.8, f"specialists ran sequentially ({elapsed:.2f}s)"
    seq = {(e["agent_name"], e["action"]): e["sequence"] for e in r.trace}
    starts = [seq[(a, "agent_started")] for a in ("InventoryAgent", "SupplierAgent", "RouteAgent")]
    publishes = [seq[(a, "evidence_published")] for a in ("InventoryAgent", "SupplierAgent", "RouteAgent")]
    assert max(starts) < min(publishes)
    assert seq[("CoordinatorAgent", "agent_started")] > max(publishes)


async def test_ownership_still_enforced_during_orchestrated_run(monkeypatch, log_path):
    async def rogue(self, kernel, bb):  # Route agent tries to write the Inventory section
        await bb.publish(AgentName.ROUTE, "inventory_findings", None)
    monkeypatch.setattr(RouteAgent, "execute", rogue)
    r = await run_incident(raw("INC-1005"), log_path=log_path)
    assert r.agent_status["RouteAgent"] == AgentStatus.FAILED
    assert "OwnershipError" in r.agent_errors["RouteAgent"]
    assert r.evidence["inventory_findings"]["warehouse_id"] == "WH-PUN-01"  # real owner's evidence intact
    assert r.decision == MitigationDecision.UNKNOWN


# --- Logging ----------------------------------------------------------------------------

async def test_run_log_contains_full_trace_exactly_once(log_path):
    r = await run_incident(raw("INC-1002"), log_path=log_path)
    entries = read_log(log_path)
    assert [e.sequence for e in entries] == list(range(len(r.trace)))
    assert {e.execution_id for e in entries} == {r.run_id}
    actions = {e.action for e in entries}
    assert {"agent_started", "evidence_published", "tool_call:get_available_routes",
            "investigation_started", "investigation_finished", "final_decision_validated",
            "run_completed"} <= actions
    final = entries[-1]
    assert final.evidence == {"outcome": "decided", "decision": "REROUTE", "selected_option_id": "RT-DEL-DEL-05"}


async def test_repeated_runs_have_unique_ids_and_no_duplicate_entries(log_path):
    log_path.parent.mkdir(parents=True)
    log_path.write_text('{"pre-existing": true}\n', encoding="utf-8")
    orch = SupplyChainOrchestrator(log_path=log_path)
    runs = [await orch.run(raw("INC-1001")) for _ in range(3)] + [await orch.run(raw("INC-1004"))]
    assert len({r.run_id for r in runs}) == 4
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"pre-existing": true}'
    entries = [AgentLogEntry.model_validate_json(x) for x in lines[1:]]
    assert len(entries) == sum(len(r.trace) for r in runs)
    keys = [(e.execution_id, e.sequence) for e in entries]
    assert len(keys) == len(set(keys))
    for r in runs:
        assert [e.sequence for e in entries if e.execution_id == r.run_id] == list(range(len(r.trace)))


async def test_persistence_error_is_surfaced(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file", encoding="utf-8")
    r = await run_incident(raw("INC-1001"), log_path=blocker / "runs.jsonl")
    assert r.decision == MitigationDecision.REORDER  # decision still reported...
    assert r.log_persisted is False and r.log_entries_written == 0  # ...but honestly not persisted
    assert "NOT persisted" in r.log_error and r.log_error in r.errors


async def test_concurrent_orchestrated_runs_share_log_without_interleaving(log_path):
    runs = await asyncio.gather(*(run_incident(raw(i), log_path=log_path) for i in INCIDENT_IDS))
    entries = read_log(log_path)
    assert len(entries) == sum(len(r.trace) for r in runs)
    assert len({r.run_id for r in runs}) == 5
    for r in runs:
        mine = [e for e in entries if e.execution_id == r.run_id]
        assert [e.sequence for e in mine] == list(range(len(r.trace)))
        assert all(e.incident_id == r.incident_id for e in mine)


def test_orchestrator_rejects_bad_agent_configuration(log_path):
    with pytest.raises(ValueError):
        SupplyChainOrchestrator(log_path=log_path, specialists=[RouteAgent(), RouteAgent()])
    with pytest.raises(ValueError):
        SupplyChainOrchestrator(log_path=log_path, specialists=[CoordinatorAgent()])


def test_orchestrator_has_no_decision_rules_of_its_own():
    import supply_chain.orchestrator as o
    src = open(o.__file__, encoding="utf-8").read()
    for marker in ("replenishment_required", "usable_stock", "lead_time", "capacity", "total_delivery_days"):
        assert marker not in src
