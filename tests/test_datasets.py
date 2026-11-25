"""
Dataset validation tests.

The oracle below is a TEST-ONLY, independent restatement of the business rules
documented in supply_chain/config.py. It proves that each sample incident has
exactly the expected outcome under those rules (i.e. the data is internally
consistent). It is not used by any agent.
"""
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from supply_chain.data.sample_data import SAMPLE_INCIDENTS, load_dataset
from supply_chain.schemas import DisruptionIncident, DisruptionType

ROOT = Path(__file__).parent.parent


def oracle(ds, inc: DisruptionIncident):
    inv = {(r.warehouse_id, r.product_id): r for r in ds.inventory}
    rec = inv.get((inc.affected_warehouse_id, inc.affected_product_id))
    available = rec.available_stock if rec else 0
    safety = rec.safety_stock if rec else 0
    replenishment = max(0, inc.required_quantity + safety - available)

    suppliers = []
    if inc.disruption_type != DisruptionType.ROUTE_BLOCKAGE and replenishment > 0:
        suppliers = sorted(
            (s for s in ds.suppliers
             if s.is_active
             and inc.affected_product_id in s.supported_products
             and s.supplier_id != inc.original_supplier_id
             and s.available_capacity >= replenishment
             and s.lead_time_days <= inc.deadline_days),
            key=lambda s: (s.lead_time_days, -s.reliability_score),
        )

    routes = sorted(
        (r for r in ds.routes
         if r.is_available
         and r.destination == inc.effective_destination
         and r.route_id != inc.original_route_id
         and (o := inv.get((r.origin, inc.affected_product_id))) is not None
         and o.usable_stock >= inc.required_quantity
         and r.total_delivery_days <= inc.deadline_days),
        key=lambda r: (r.total_delivery_days, -r.availability_score),
    )

    if inc.disruption_type == DisruptionType.ROUTE_BLOCKAGE:
        order = [("REROUTE", routes)]
    else:
        order = [("REORDER", suppliers), ("REROUTE", routes)]
    for decision, options in order:
        if options:
            best = options[0]
            return decision, getattr(best, "supplier_id", None) or best.route_id
    return "HOLD", None


def test_dataset_loads_and_is_large_enough(dataset):
    assert len(dataset.incidents) >= 3
    assert len(dataset.inventory) >= 5 and len(dataset.suppliers) >= 4 and len(dataset.routes) >= 5


def test_incidents_cover_all_disruption_types(dataset):
    assert {i.disruption_type for i in dataset.incidents} == set(DisruptionType)


def test_every_incident_has_expected_outcome_covering_all_decisions(dataset, expected_outcomes):
    assert set(expected_outcomes) == {i.incident_id for i in dataset.incidents}
    assert {o["expected_decision"] for o in expected_outcomes.values()} == {"REORDER", "REROUTE", "HOLD"}


@pytest.mark.parametrize("incident_id", [d["incident_id"] for d in SAMPLE_INCIDENTS])
def test_incident_outcome_is_consistent_with_business_rules(dataset, expected_outcomes, incident_id):
    inc = next(i for i in dataset.incidents if i.incident_id == incident_id)
    exp = expected_outcomes[incident_id]
    assert oracle(dataset, inc) == (exp["expected_decision"], exp["expected_option_id"])


def test_sample_incidents_construct_as_schema_objects():
    assert all(DisruptionIncident(**d) for d in SAMPLE_INCIDENTS)


def test_reported_blocked_routes_and_disrupted_suppliers_are_unusable(dataset):
    routes = {r.route_id: r for r in dataset.routes}
    for inc in dataset.incidents:
        if inc.disruption_type == DisruptionType.ROUTE_BLOCKAGE:
            assert routes[inc.original_route_id].is_available is False


# --- Negative tests: corrupted copies must be rejected --------------------

def _edit(path: Path, fn):
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize("file,mutation,message", [
    ("inventory.json", lambda d: d[0].update(warehouse_id="WH-XYZ-99"), "unknown warehouse"),
    ("inventory.json", lambda d: d.append(dict(d[0])), "duplicate inventory"),
    ("routes.json", lambda d: d[1].update(route_id=d[0]["route_id"]), "duplicate route_id"),
    ("routes.json", lambda d: d[0].update(is_available=True, availability_score=0.9), "marked available"),
    ("suppliers.json", lambda d: d[0]["supported_products"].append("DRONE-999"), "unknown product"),
    ("incidents.json", lambda d: d[0].update(original_supplier_id="SUP-03"), "does not supply"),
    ("incidents.json", lambda d: d[1].update(affected_warehouse_id="WH-MUM-02"), "does not start at"),
])
def test_inconsistent_data_is_rejected(data_copy, file, mutation, message):
    _edit(data_copy / file, mutation)
    with pytest.raises(ValidationError, match=message):
        load_dataset(data_copy)


# --- Secret hygiene -------------------------------------------------------

def test_runtime_code_never_reads_expected_outcomes():
    for py in (ROOT / "supply_chain").rglob("*.py"):
        assert "expected_outcomes" not in py.read_text(encoding="utf-8"), py


def test_no_api_keys_hardcoded_in_source():
    key_pattern = re.compile(r"(AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9_\-]{20,})")
    files = [*ROOT.glob("*.txt"), ROOT / ".env.example", *(ROOT / "supply_chain").rglob("*.py"),
             *(ROOT / "supply_chain").rglob("*.json"), *(ROOT / "tests").rglob("*.py")]
    offenders = [str(f) for f in files if f.is_file() and key_pattern.search(f.read_text(encoding="utf-8"))]
    assert offenders == []


def test_env_file_is_gitignored_and_example_has_no_values():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore and "!.env.example" in ignore
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip().endswith("API_KEY="):
            continue
        assert "API_KEY=" not in line, f"example must not contain key values: {line}"


def test_placeholder_keys_are_treated_as_missing(monkeypatch):
    from supply_chain import config
    monkeypatch.setenv("GEMINI_API_KEY", "your-gemini-key-here")
    monkeypatch.delenv("GOOGLE_AI_API_KEY", raising=False)
    assert config._read_secret("GEMINI_API_KEY", "GOOGLE_AI_API_KEY") == ""
    assert config.LLM_MODE == "deterministic_fallback"
