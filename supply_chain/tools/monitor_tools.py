"""Monitor Agent tools: validate_disruption, identify_affected_sites."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import ValidationError

from supply_chain.schemas import DisruptionIncident, DisruptionType, SupplyChainDataset
from supply_chain.tools.common import (
    AffectedSites,
    DisruptionValidation,
    ToolInputError,
    dataset_or_default,
)

IncidentLike = Union[DisruptionIncident, Dict[str, Any]]


def _severity(required_quantity: int, deadline_days: int) -> str:
    if required_quantity >= 100 or deadline_days <= 3:
        return "HIGH"
    if required_quantity >= 50 or deadline_days <= 7:
        return "MEDIUM"
    return "LOW"


def validate_disruption(incident: IncidentLike,
                        dataset: Optional[SupplyChainDataset] = None) -> DisruptionValidation:
    """Validate a disruption report: schema, then references against master/network data."""
    ds = dataset_or_default(dataset)
    errors: List[str] = []
    raw_id = incident.incident_id if isinstance(incident, DisruptionIncident) else (
        incident.get("incident_id") if isinstance(incident, dict) else None)
    if isinstance(incident, DisruptionIncident):
        inc: Optional[DisruptionIncident] = incident
    elif isinstance(incident, dict):
        try:
            inc = DisruptionIncident.model_validate(incident)
        except ValidationError as exc:
            inc = None
            errors.extend(f"{'.'.join(map(str, e['loc'])) or 'report'}: {e['msg']}" for e in exc.errors())
    else:
        raise ToolInputError("incident must be a DisruptionIncident or a dict")

    if inc is not None:
        products = {p.product_id for p in ds.products}
        warehouses = {s.site_id for s in ds.sites if s.site_type == "warehouse"}
        sites = {s.site_id for s in ds.sites}
        suppliers = {s.supplier_id: s for s in ds.suppliers}
        routes = {r.route_id: r for r in ds.routes}
        if inc.affected_product_id not in products:
            errors.append(f"unknown product {inc.affected_product_id}")
        if inc.affected_warehouse_id not in warehouses:
            errors.append(f"unknown warehouse {inc.affected_warehouse_id}")
        if inc.destination_id and inc.destination_id not in sites:
            errors.append(f"unknown destination {inc.destination_id}")
        if inc.original_supplier_id:
            sup = suppliers.get(inc.original_supplier_id)
            if sup is None:
                errors.append(f"unknown supplier {inc.original_supplier_id}")
            elif inc.affected_product_id not in sup.supported_products:
                errors.append(f"{sup.supplier_id} does not supply {inc.affected_product_id}")
        if inc.original_route_id:
            rt = routes.get(inc.original_route_id)
            if rt is None:
                errors.append(f"unknown route {inc.original_route_id}")
            else:
                if rt.origin != inc.affected_warehouse_id:
                    errors.append(f"route {rt.route_id} does not start at {inc.affected_warehouse_id}")
                if inc.destination_id and rt.destination != inc.destination_id:
                    errors.append(f"route {rt.route_id} does not end at {inc.destination_id}")
                if inc.disruption_type == DisruptionType.ROUTE_BLOCKAGE and rt.is_available:
                    errors.append(f"reported blocked route {rt.route_id} is available in network data")

    is_valid = not errors
    severity = _severity(inc.required_quantity, inc.deadline_days) if (is_valid and inc) else "UNKNOWN"
    immediate = bool(is_valid and inc and inc.deadline_days <= 3)
    msg = (f"Incident {raw_id} validated. Severity: {severity}." if is_valid
           else f"Incident {raw_id} failed validation: {'; '.join(errors)}")
    return DisruptionValidation(incident_id=raw_id if isinstance(raw_id, str) else None, is_valid=is_valid,
                                errors=errors, severity=severity, requires_immediate_action=immediate,
                                validation_message=msg)


def identify_affected_sites(incident: DisruptionIncident,
                            dataset: Optional[SupplyChainDataset] = None) -> AffectedSites:
    """Derive affected sites, blocked entity, related suppliers and routes from network data."""
    if not isinstance(incident, DisruptionIncident):
        raise ToolInputError("identify_affected_sites requires a validated DisruptionIncident")
    ds = dataset_or_default(dataset)
    dest = incident.effective_destination
    sites = [incident.affected_warehouse_id] + ([dest] if dest != incident.affected_warehouse_id else [])
    if incident.disruption_type == DisruptionType.SUPPLIER_DELAY:
        blocked = incident.original_supplier_id
    elif incident.disruption_type == DisruptionType.ROUTE_BLOCKAGE:
        blocked = incident.original_route_id
    else:
        blocked = None
    related_suppliers = sorted(s.supplier_id for s in ds.suppliers
                               if incident.affected_product_id in s.supported_products)
    related_routes = sorted(r.route_id for r in ds.routes
                            if r.origin in sites or r.destination in sites)
    desc = (f"{incident.disruption_type.value} affecting {incident.affected_product_id} at "
            f"{incident.affected_warehouse_id} (deliver to {dest}). Blocked entity: {blocked or 'none'}. "
            f"{len(related_suppliers)} supplier(s) and {len(related_routes)} route(s) are related.")
    return AffectedSites(incident_id=incident.incident_id, affected_sites=sites, destination_id=dest,
                         blocked_entity=blocked, related_suppliers=related_suppliers,
                         related_routes=related_routes, impact_description=desc)
