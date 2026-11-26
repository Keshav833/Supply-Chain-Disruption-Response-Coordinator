"""PHASE_4: unit tests for all ten tools (real computations over the datasets)."""
import json

import pytest
from pydantic import ValidationError

from supply_chain.blackboard import Blackboard
from supply_chain.contracts import REQUIRED_TOOLS, AgentName
from supply_chain.data.sample_data import load_dataset
from supply_chain.schemas import DisruptionIncident, EvidenceStatus, MitigationDecision
from supply_chain.tools import (
    coordinator_tools,
    cost_tools,
    inventory_tools,
    monitor_tools,
    route_tools,
    supplier_tools,
)
from supply_chain.tools.common import StockShortage, ToolInputError
from supply_chain.tools.coordinator_tools import read_blackboard_evidence, validate_mitigation_decision
from supply_chain.tools.inventory_tools import calculate_stock_shortage, get_inventory
from supply_chain.tools.monitor_tools import identify_affected_sites, validate_disruption
from supply_chain.tools.route_tools import calculate_route_delay, get_available_routes
from supply_chain.tools.supplier_tools import check_supplier_capacity, find_alternative_suppliers

MODULES = {AgentName.MONITOR: monitor_tools, AgentName.INVENTORY: inventory_tools,
           AgentName.SUPPLIER: supplier_tools, AgentName.ROUTE: route_tools,
           AgentName.COST: cost_tools, AgentName.COORDINATOR: coordinator_tools}


def incident(**kw):
    base = dict(incident_id="INC-1005", disruption_type="route_blockage", affected_product_id="LAPTOP-001",
                affected_warehouse_id="WH-PUN-01", required_quantity=30, destination_id="CUST-MUM",
                original_route_id="RT-PUN-MUM-01", deadline_days=5)
    return {**base, **kw}


def test_every_agent_has_two_real_tool_functions():
    assert len(REQUIRED_TOOLS) == 6
    for agent, tools in REQUIRED_TOOLS.items():
        assert len(tools) == 2
        for t in tools:
            assert callable(getattr(MODULES[agent], t)), (agent, t)


# --- Monitor ---------------------------------------------------------------

def test_validate_disruption_accepts_all_sample_incidents(dataset):
    for inc in dataset.incidents:
        r = validate_disruption(inc)
        assert r.is_valid and r.errors == [] and r.severity in {"LOW", "MEDIUM", "HIGH"}


def test_validate_disruption_severity_and_urgency():
    assert validate_disruption(incident(required_quantity=30, deadline_days=10)).severity == "LOW"
    assert validate_disruption(incident(required_quantity=60, deadline_days=10)).severity == "MEDIUM"
    r = validate_disruption(incident(deadline_days=2))
    assert r.severity == "HIGH" and r.requires_immediate_action


@pytest.mark.parametrize("change,msg", [
    (dict(affected_product_id="DRONE-999"), "unknown product"),
    (dict(affected_warehouse_id="WH-XYZ-09", original_route_id="RT-PUN-MUM-01"), "unknown warehouse"),
    (dict(original_route_id="RT-MUM-MUM-02"), "available in network data"),
    (dict(original_route_id="RT-ZZZ-ZZZ-99"), "unknown route"),
    (dict(destination_id="CUST-DEL"), "does not end at"),
    (dict(required_quantity=0), "required_quantity"),
    (dict(disruption_type="earthquake"), "disruption_type"),
])
def test_validate_disruption_rejects_bad_reports(change, msg):
    r = validate_disruption(incident(**change))
    assert not r.is_valid and r.severity == "UNKNOWN" and not r.requires_immediate_action
    assert msg in " ".join(r.errors)


def test_validate_disruption_rejects_non_report_input():
    with pytest.raises(ToolInputError):
        validate_disruption("INC-1001")


def test_identify_affected_sites_derives_from_network_data():
    r = identify_affected_sites(DisruptionIncident(**incident()))
    assert r.affected_sites == ["WH-PUN-01", "CUST-MUM"]
    assert r.blocked_entity == "RT-PUN-MUM-01"
    assert r.related_suppliers == ["SUP-01", "SUP-02", "SUP-04"]
    assert r.related_routes == ["RT-DEL-MUM-03", "RT-MUM-MUM-02", "RT-MUM-PUN-06", "RT-PUN-MUM-01"]
    with pytest.raises(ToolInputError):
        identify_affected_sites(incident())


# --- Inventory -------------------------------------------------------------

def test_get_inventory_single_and_network():
    one = get_inventory("LAPTOP-001", "WH-PUN-01")
    assert [(s.warehouse_id, s.available_stock, s.safety_stock, s.usable_stock) for s in one.records] == \
        [("WH-PUN-01", 25, 40, 0)]
    allw = get_inventory("LAPTOP-001")
    assert [(s.warehouse_id, s.usable_stock) for s in allw.records] == \
        [("WH-MUM-02", 90), ("WH-DEL-03", 60), ("WH-PUN-01", 0)]
    assert get_inventory("TABLET-003", "WH-MUM-02").records == []  # missing, not zero


@pytest.mark.parametrize("args", [("laptop", None), ("LAPTOP-001", "Pune"), (None, None)])
def test_get_inventory_validates_ids(args):
    with pytest.raises(ToolInputError):
        get_inventory(*args)


@pytest.mark.parametrize("avail,safety,req,usable,short,repl,met,fulfil", [
    (25, 40, 100, 0, 115, 115, False, False),
    (80, 20, 240, 60, 180, 180, True, False),
    (200, 50, 50, 150, -100, 0, True, True),
    (10, 20, 80, 0, 90, 90, False, False),
])
def test_calculate_stock_shortage(avail, safety, req, usable, short, repl, met, fulfil):
    r = calculate_stock_shortage(avail, safety, req)
    assert (r.usable_stock, r.shortage, r.replenishment_required, r.safety_stock_met,
            r.can_fulfil_from_stock) == (usable, short, repl, met, fulfil)


@pytest.mark.parametrize("args", [(-1, 0, 1), (1, -1, 1), (1, 1, 0), ("5", 1, 1), (True, 1, 1), (1.5, 1, 1)])
def test_calculate_stock_shortage_validates_inputs(args):
    with pytest.raises(ToolInputError):
        calculate_stock_shortage(*args)


def test_stock_shortage_output_model_rejects_inconsistent_values():
    with pytest.raises(ValidationError):
        StockShortage(available_stock=25, safety_stock=40, required_quantity=100, usable_stock=0, shortage=1,
                      replenishment_required=1, safety_stock_met=False, can_fulfil_from_stock=False)


# --- Supplier --------------------------------------------------------------

def test_find_alternative_suppliers_excludes_disrupted_and_inactive():
    r = find_alternative_suppliers("LAPTOP-001", ["SUP-04"])
    assert [s.supplier_id for s in r.candidates] == ["SUP-02"]
    assert {x.option_id: x.reason for x in r.rejected} == {"SUP-01": "supplier inactive",
                                                          "SUP-04": "disrupted/excluded supplier"}
    r2 = find_alternative_suppliers("TABLET-003", [])
    assert [s.supplier_id for s in r2.candidates] == ["SUP-03", "SUP-05"]  # sorted by lead time


@pytest.mark.parametrize("args", [("LAPTOP-001", "SUP-04"), ("LAPTOP-001", ["ACME"]), ("x", [])])
def test_find_alternative_suppliers_validates(args):
    with pytest.raises(ToolInputError):
        find_alternative_suppliers(*args)


def test_check_supplier_capacity_reports_each_constraint():
    r = check_supplier_capacity(["SUP-02", "SUP-04", "SUP-01"], "LAPTOP-001", 240, 5)
    by = {c.supplier_id: c for c in r.checks}
    assert (by["SUP-02"].sufficient_capacity, by["SUP-02"].within_deadline, by["SUP-02"].feasible) == \
        (False, True, False)
    assert by["SUP-04"].within_deadline is False and "lead time 15d > deadline 5d" in by["SUP-04"].reason
    assert by["SUP-01"].is_active is False
    ok = check_supplier_capacity(["SUP-02"], "LAPTOP-001", 100, 7).checks[0]
    assert ok.feasible and ok.available_capacity == 150 and ok.lead_time_days == 3
    assert check_supplier_capacity(["SUP-03"], "LAPTOP-001", 1, 9).checks[0].supports_product is False
    assert check_supplier_capacity([], "LAPTOP-001", 1, 1).checks == []


@pytest.mark.parametrize("args", [(["SUP-99"], "LAPTOP-001", 1, 1), (["SUP-02", "SUP-02"], "LAPTOP-001", 1, 1),
                                  (["SUP-02"], "LAPTOP-001", 0, 1), (["SUP-02"], "LAPTOP-001", 1, 0)])
def test_check_supplier_capacity_validates(args):
    with pytest.raises(ToolInputError):
        check_supplier_capacity(*args)


# --- Route -----------------------------------------------------------------

def test_get_available_routes_annotates_origin_stock():
    r = get_available_routes("CUST-MUM", "LAPTOP-001", 30, ["RT-PUN-MUM-01"])
    assert [(c.route.route_id, c.origin_usable_stock, c.stock_sufficient) for c in r.candidates] == \
        [("RT-MUM-MUM-02", 90, True), ("RT-DEL-MUM-03", 60, True)]
    assert [x.option_id for x in r.rejected] == ["RT-PUN-MUM-01"]
    big = get_available_routes("CUST-MUM", "LAPTOP-001", 95, ["RT-PUN-MUM-01"])
    assert [c.stock_sufficient for c in big.candidates] == [False, False]
    # Origin without any record of the product counts as 0 usable units.
    tab = get_available_routes("WH-PUN-01", "TABLET-003", 80)
    assert [(c.route.route_id, c.origin_usable_stock, c.stock_sufficient) for c in tab.candidates] == \
        [("RT-MUM-PUN-06", 0, False)]
    assert get_available_routes("WH-DEL-03", "LAPTOP-001", 240).candidates == []


def test_get_available_routes_skips_unavailable_routes_without_exclusion():
    r = get_available_routes("CUST-DEL", "PHONE-002", 50)
    assert [c.route.route_id for c in r.candidates] == ["RT-DEL-DEL-05"]
    assert {x.option_id: x.reason for x in r.rejected} == {"RT-BLR-DEL-04": "route unavailable"}


@pytest.mark.parametrize("args", [("Mumbai", "LAPTOP-001", 1), ("CUST-MUM", "LAPTOP-001", 0),
                                  ("CUST-MUM", "LAPTOP-001", 1, ["bad"])])
def test_get_available_routes_validates(args):
    with pytest.raises(ToolInputError):
        get_available_routes(*args)


def test_calculate_route_delay():
    r = calculate_route_delay(["RT-DEL-MUM-03", "RT-PUN-MUM-01"], 4)
    a, b = r.delays
    assert (a.total_delivery_days, a.slack_days, a.within_deadline) == (4, 0, True)
    assert (b.total_delivery_days, b.is_available, b.within_deadline) == (6, False, False)
    assert calculate_route_delay(["RT-DEL-MUM-03"], 3).delays[0].within_deadline is False
    assert calculate_route_delay([], 3).delays == []


@pytest.mark.parametrize("args", [(["RT-ZZZ-ZZZ-01"], 3), (["RT-DEL-MUM-03"], 0), ("RT-DEL-MUM-03", 3),
                                  (["RT-DEL-MUM-03", "RT-DEL-MUM-03"], 3)])
def test_calculate_route_delay_validates(args):
    with pytest.raises(ToolInputError):
        calculate_route_delay(*args)


def test_route_tools_use_injected_dataset(data_copy):
    routes = json.loads((data_copy / "routes.json").read_text())
    routes[1]["current_delay_days"] = 9
    (data_copy / "routes.json").write_text(json.dumps(routes))
    ds = load_dataset(data_copy)
    assert calculate_route_delay(["RT-MUM-MUM-02"], 5, ds).delays[0].total_delivery_days == 10


# --- Coordinator -----------------------------------------------------------

async def _board_with_all_evidence(inc_id="INC-1001"):
    from tests.helpers import run_pipeline
    from supply_chain.data.sample_data import SAMPLE_INCIDENTS
    d = next(x for x in SAMPLE_INCIDENTS if x["incident_id"] == inc_id)
    return (await run_pipeline(DisruptionIncident(**d)))["blackboard"]


async def test_read_blackboard_evidence_unknown_on_empty_board():
    bb = Blackboard(DisruptionIncident(**incident()))
    r = read_blackboard_evidence(bb)
    assert r.sufficient_for_decision is False
    assert set(r.evidence_status.values()) == {EvidenceStatus.UNKNOWN}
    assert len(r.missing_sections) == 4 and r.failed_sections == []
    with pytest.raises(ToolInputError):
        read_blackboard_evidence({"inventory_findings": {}})


async def test_read_blackboard_evidence_full_board_is_sufficient():
    r = read_blackboard_evidence(await _board_with_all_evidence())
    assert r.sufficient_for_decision and r.contradictions == []
    assert set(r.evidence_status.values()) == {EvidenceStatus.AVAILABLE}


async def test_validate_mitigation_decision_rules():
    r = read_blackboard_evidence(await _board_with_all_evidence("INC-1001"))
    assert validate_mitigation_decision("REORDER", "SUP-02", r).is_valid
    bad = validate_mitigation_decision("REORDER", "SUP-01", r)
    assert not bad.is_valid and any("disrupted" in v for v in bad.violations)
    route = validate_mitigation_decision("REROUTE", "RT-MUM-PUN-06", r)
    assert any("90 usable units < required 100" in v for v in route.violations)
    hold = validate_mitigation_decision("HOLD", None, r)
    assert not hold.is_valid and hold.feasible_suppliers == ["SUP-02"]
    assert not validate_mitigation_decision("UNKNOWN", None, r).is_valid
    assert not validate_mitigation_decision("REORDER", None, r).is_valid
    with pytest.raises(ToolInputError):
        validate_mitigation_decision("APPROVE", None, r)


async def test_validate_mitigation_decision_route_blockage_rejects_reorder():
    r = read_blackboard_evidence(await _board_with_all_evidence("INC-1005"))
    v = validate_mitigation_decision("REORDER", "SUP-02", r)
    assert "REORDER does not resolve a route blockage" in v.violations
    assert validate_mitigation_decision("REROUTE", "RT-MUM-MUM-02", r).is_valid
    assert validate_mitigation_decision("REROUTE", "RT-DEL-MUM-03", r).is_valid  # feasible but slower
    assert not validate_mitigation_decision("REROUTE", "RT-PUN-MUM-01", r).is_valid


async def test_validate_mitigation_decision_insufficient_evidence_only_unknown():
    bb = Blackboard(DisruptionIncident(**incident()))
    r = read_blackboard_evidence(bb)
    assert validate_mitigation_decision("UNKNOWN", None, r).is_valid
    for d, opt in (("REROUTE", "RT-MUM-MUM-02"), ("HOLD", None), ("REORDER", "SUP-02")):
        v = validate_mitigation_decision(d, opt, r)
        assert not v.is_valid and "insufficient evidence" in v.violations[0]
    assert MitigationDecision.UNKNOWN.value == "UNKNOWN"
