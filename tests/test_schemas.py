import pytest
from pydantic import ValidationError

from supply_chain.schemas import (
    DisruptionIncident,
    DisruptionType,
    ExecutionMode,
    IncidentAssessment,
    InventoryRecord,
    MitigationDecision,
    MitigationResult,
    Notification,
    RouteFindings,
    RouteRecord,
    Site,
    StockLocation,
    SupplierRecord,
)


def incident(**overrides):
    data = dict(
        incident_id="INC-1001",
        disruption_type="supplier_delay",
        affected_product_id="LAPTOP-001",
        affected_warehouse_id="WH-PUN-01",
        required_quantity=100,
        original_supplier_id="SUP-01",
        deadline_days=7,
    )
    data.update(overrides)
    return DisruptionIncident(**data)


def route(**overrides):
    data = dict(route_id="RT-MUM-MUM-02", origin="WH-MUM-02", destination="CUST-MUM",
                is_available=True, normal_transit_days=1, current_delay_days=2, availability_score=0.9)
    data.update(overrides)
    return RouteRecord(**data)


# --- Incident -------------------------------------------------------------

def test_valid_incident_parses_and_defaults_destination_to_warehouse():
    inc = incident()
    assert inc.disruption_type is DisruptionType.SUPPLIER_DELAY
    assert inc.effective_destination == "WH-PUN-01"
    assert inc.reported_at.tzinfo is not None


@pytest.mark.parametrize("field,value", [
    ("incident_id", "1001"),
    ("affected_warehouse_id", "PUNE"),
    ("required_quantity", 0),
    ("deadline_days", 0),
    ("disruption_type", "earthquake"),
    ("reported_delay_days", -1),
])
def test_incident_rejects_bad_fields(field, value):
    with pytest.raises(ValidationError):
        incident(**{field: value})


def test_supplier_delay_requires_original_supplier():
    with pytest.raises(ValidationError, match="original_supplier_id"):
        incident(original_supplier_id=None)


def test_route_blockage_requires_route_and_destination():
    base = dict(disruption_type="route_blockage", original_supplier_id=None)
    with pytest.raises(ValidationError, match="original_route_id"):
        incident(**base, destination_id="CUST-MUM")
    with pytest.raises(ValidationError, match="destination_id"):
        incident(**base, original_route_id="RT-PUN-MUM-01")
    ok = incident(**base, original_route_id="RT-PUN-MUM-01", destination_id="CUST-MUM")
    assert ok.effective_destination == "CUST-MUM"


# --- Master data / inventory / suppliers / routes -------------------------

def test_site_type_must_match_id():
    with pytest.raises(ValidationError):
        Site(site_id="CUST-MUM", name="x", city="Mumbai", site_type="warehouse")


def test_inventory_usable_stock_and_non_negative():
    assert InventoryRecord(warehouse_id="WH-PUN-01", product_id="LAPTOP-001",
                           available_stock=25, safety_stock=40).usable_stock == 0
    assert InventoryRecord(warehouse_id="WH-MUM-02", product_id="LAPTOP-001",
                           available_stock=120, safety_stock=30).usable_stock == 90
    with pytest.raises(ValidationError):
        InventoryRecord(warehouse_id="WH-PUN-01", product_id="LAPTOP-001",
                        available_stock=-1, safety_stock=0)


def test_stock_location_usable_must_be_consistent():
    StockLocation(warehouse_id="WH-MUM-02", available_stock=120, safety_stock=30, usable_stock=90)
    with pytest.raises(ValidationError, match="usable_stock"):
        StockLocation(warehouse_id="WH-MUM-02", available_stock=120, safety_stock=30, usable_stock=120)


def test_supplier_validation():
    base = dict(supplier_id="SUP-02", name="GlobalTech", supported_products=["LAPTOP-001"],
                available_capacity=150, lead_time_days=3, is_active=True)
    SupplierRecord(**base)
    for bad in ({"lead_time_days": 0}, {"available_capacity": -5},
                {"supported_products": []}, {"supported_products": ["LAPTOP-001", "LAPTOP-001"]},
                {"reliability_score": 1.5}):
        with pytest.raises(ValidationError):
            SupplierRecord(**{**base, **bad})


def test_route_consistency_rules():
    assert route().total_delivery_days == 3
    with pytest.raises(ValidationError, match="blocked route"):
        route(is_available=False, availability_score=0.5)
    with pytest.raises(ValidationError, match="differ"):
        route(origin="WH-MUM-02", destination="WH-MUM-02")
    with pytest.raises(ValidationError):
        route(origin="CUST-MUM")  # routes start at warehouses


def test_route_findings_cannot_recommend_blocked_route():
    blocked = route(route_id="RT-PUN-MUM-01", origin="WH-PUN-01", is_available=False, availability_score=0.0)
    with pytest.raises(ValidationError, match="blocked"):
        RouteFindings(available_routes=[], best_route=blocked, minimum_delivery_days=0,
                      can_deliver_by_deadline=False, recommendation="x")


def test_assessment_severity_is_constrained():
    kw = dict(incident_id="INC-1001", is_valid=True, validation_message="ok",
              affected_sites=["WH-PUN-01"], requires_immediate_action=True)
    IncidentAssessment(severity="HIGH", **kw)
    with pytest.raises(ValidationError):
        IncidentAssessment(severity="CRITICAL", **kw)


# --- Decision / fallback labelling ----------------------------------------

def decision(**overrides):
    data = dict(incident_id="INC-1001", decision="REORDER", justification="SUP-02 can supply 150 in 3 days.",
                parties_to_notify=["Procurement"], evidence_summary={})
    data.update(overrides)
    return MitigationResult(**data)


def test_decision_defaults_to_explicit_deterministic_fallback_label():
    d = decision()
    assert d.decision is MitigationDecision.REORDER
    assert d.execution_mode is ExecutionMode.DETERMINISTIC_FALLBACK
    assert d.authorized is False


def test_legacy_deterministic_label_is_mapped_and_bare_llm_label_rejected():
    assert decision(execution_mode="deterministic").execution_mode is ExecutionMode.DETERMINISTIC_FALLBACK
    with pytest.raises(ValidationError):
        decision(execution_mode="llm")
    assert decision(execution_mode="semantic_kernel_llm").execution_mode is ExecutionMode.SEMANTIC_KERNEL_LLM


def test_decision_cannot_claim_authorization_or_empty_justification():
    with pytest.raises(ValidationError, match="authorized"):
        decision(authorized=True)
    with pytest.raises(ValidationError):
        decision(justification="")


def test_notifications_are_simulated_and_never_marked_delivered():
    n = Notification(party="Procurement", reason="REORDER recommended")
    assert n.channel == "simulated" and n.delivered is False
    with pytest.raises(ValidationError):
        Notification(party="Procurement", reason="x", delivered=True)
