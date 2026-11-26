"""Cost Agent tests: contracts, ordering, feasibility filtering, failure isolation, rationale consistency."""
from __future__ import annotations

import pytest

from supply_chain.agents import cost_agent as cost_agent_module
from supply_chain.contracts import BLACKBOARD_SECTION, REQUIRED_TOOLS, AgentName
from supply_chain.data.sample_data import SAMPLE_INCIDENTS, load_dataset
from supply_chain.orchestrator import SupplyChainOrchestrator, run_incident
from supply_chain.schemas import (
    AgentStatus,
    DisruptionIncident,
    ExecutionMode,
    MitigationDecision,
)
from supply_chain.tools.cost_tools import compare_options, estimate_option_cost


def _inc(inc_id: str) -> dict:
    return next(d for d in SAMPLE_INCIDENTS if d["incident_id"] == inc_id)


def test_cost_agent_contract_has_two_tools_and_one_section():
    assert AgentName.COST in REQUIRED_TOOLS
    assert REQUIRED_TOOLS[AgentName.COST] == ("estimate_option_cost", "compare_options")
    assert len(REQUIRED_TOOLS[AgentName.COST]) == 2
    assert BLACKBOARD_SECTION[AgentName.COST] == "cost_findings"


async def test_cost_agent_runs_after_three_specialists(tmp_path):
    r = await run_incident(_inc("INC-1001"), log_path=tmp_path / "runs.jsonl")
    seq_by_published = {
        e["agent_name"]: e["sequence"]
        for e in r.trace
        if e["action"] == "evidence_published"
    }
    cost_start = next(
        e["sequence"] for e in r.trace
        if e["agent_name"] == "CostAgent" and e["action"] == "agent_started"
    )
    for specialist in ("InventoryAgent", "SupplierAgent", "RouteAgent"):
        assert seq_by_published[specialist] < cost_start
    assert seq_by_published["CostAgent"] < seq_by_published["CoordinatorAgent"]


def test_cheapest_feasible_option_is_selected_over_more_expensive():
    ds = load_dataset()
    # Both SUP-02 (REORDER) and RT-DEL-DEL-05 (REROUTE) exist; logistics route is much cheaper than procurement
    findings = compare_options(
        {"SUP-02": ("REORDER", 3, 7), "RT-DEL-DEL-05": ("REROUTE", 1, 5)},
        required_quantity=50,
        incident_id="INC-1002",
        dataset=ds,
    )
    assert findings.cheapest_option == "RT-DEL-DEL-05"
    assert findings.cost_comparison["RT-DEL-DEL-05"] < findings.cost_comparison["SUP-02"]


async def test_cheaper_infeasible_option_is_never_chosen(tmp_path):
    # In INC-1001, RT-MUM-PUN-06 (logistics) would be far cheaper than SUP-02 ($57,500 procurement),
    # but RT-MUM-PUN-06 is infeasible (90 usable < 100 required), so CostAgent excludes it and
    # Coordinator never chooses it.
    r = await run_incident(_inc("INC-1001"), log_path=tmp_path / "runs.jsonl")
    cost_findings = r.evidence["cost_findings"]
    assert "RT-MUM-PUN-06" not in cost_findings["cost_comparison"]
    assert cost_findings["cheapest_option"] == "SUP-02"
    assert r.decision == MitigationDecision.REORDER
    assert r.selected_option_id == "SUP-02"


async def test_cost_failure_leaves_decision_rule_based_and_labelled(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("cost service unavailable")

    monkeypatch.setattr(cost_agent_module, "compare_options", boom)
    # Also patch in cost_tools where CostPlugin imports or calls it
    from supply_chain.tools import cost_tools
    monkeypatch.setattr(cost_tools, "compare_options", boom)

    r = await run_incident(_inc("INC-1001"), log_path=tmp_path / "runs.jsonl")
    assert r.agent_status["CostAgent"] == AgentStatus.FAILED
    assert r.evidence_status["cost_findings"] == "FAILED"
    assert r.evidence["cost_findings"] is None
    assert r.decision == MitigationDecision.REORDER
    assert r.selected_option_id == "SUP-02"
    assert r.agent_execution_modes["CoordinatorAgent"] == ExecutionMode.DETERMINISTIC_FALLBACK
    assert r.execution_label.startswith("deterministic_fallback")


async def test_rationale_cost_figures_match_cost_findings(tmp_path):
    r = await run_incident(_inc("INC-1001"), log_path=tmp_path / "runs.jsonl")
    cost_findings = r.evidence["cost_findings"]
    selected = r.selected_option_id
    expected_cost = cost_findings["cost_comparison"][selected]
    assert expected_cost == 66125.0
    assert cost_findings["cost_estimates"][0]["breakdown"] == {"base_cost": 57500.0, "expedited_fee": 8625.0}
    assert f"{r.evidence['inventory_findings']['replenishment_required']} units" in r.justification
    assert f"${expected_cost:,.2f}" in r.justification
    assert selected in r.justification


@pytest.mark.parametrize("inc_id", ["INC-1003", "INC-1004"])
async def test_informational_estimate_does_not_change_hold_decision(inc_id, tmp_path):
    r = await run_incident(_inc(inc_id), log_path=tmp_path / "runs.jsonl")
    assert r.decision == MitigationDecision.HOLD
    assert r.selected_option_id is None
    assert r.agent_status["CostAgent"] == AgentStatus.COMPLETE
    assert r.evidence_status["cost_findings"] == "AVAILABLE"
    assert r.evidence["cost_findings"]["cheapest_option"] is None
    assert r.evidence["cost_findings"]["cost_estimates"] == []
    assert "estimate_option_cost" in r.tools_invoked["CostAgent"]
    assert "compare_options" in r.tools_invoked["CostAgent"]
