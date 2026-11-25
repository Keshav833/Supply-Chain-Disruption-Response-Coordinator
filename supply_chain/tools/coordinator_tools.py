"""Coordinator Agent tools: read_blackboard_evidence, validate_mitigation_decision.

These tools see ONLY the Blackboard (published evidence). They never import or
read the raw inventory, supplier or route datasets.

Feasibility rules (re-checked from evidence, not trusted from agent flags):
    REORDER : not a route blockage; supplier not excluded; supplier active and
              supports the product; capacity >= inventory replenishment_required;
              lead time <= incident deadline.
    REROUTE : route has an assessment; not a reported blocked route; ends at the
              required destination; ORIGIN usable stock >= required quantity;
              total delivery days <= incident deadline.
    HOLD    : no option is feasible.
    UNKNOWN : evidence is missing, failed, invalid or contradictory.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from supply_chain.contracts import BlackboardEvidence
from supply_chain.schemas import DisruptionType, EvidenceStatus, MitigationDecision
from supply_chain.tools.common import ToolInputError

if TYPE_CHECKING:  # pragma: no cover
    from supply_chain.blackboard import Blackboard

REQUIRED_EVIDENCE = ("incident_assessment", "inventory_findings", "supplier_findings", "route_findings")


class EvidenceReport(BaseModel):
    incident_id: str
    evidence: BlackboardEvidence
    evidence_status: Dict[str, EvidenceStatus]
    missing_sections: List[str]
    failed_sections: List[str]
    contradictions: List[str]
    sufficient_for_decision: bool

    @model_validator(mode="after")
    def _consistent(self) -> "EvidenceReport":
        expected = not self.missing_sections and not self.failed_sections and not self.contradictions
        if self.sufficient_for_decision != expected:
            raise ValueError("sufficient_for_decision must reflect missing/failed/contradictions")
        return self


class DecisionValidation(BaseModel):
    decision: MitigationDecision
    option_id: Optional[str]
    is_valid: bool
    violations: List[str]
    feasible_suppliers: List[str] = Field(default_factory=list)
    feasible_routes: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "DecisionValidation":
        if self.is_valid == bool(self.violations):
            raise ValueError("is_valid must be True iff there are no violations")
        return self


# --------------------------------------------------------------------------- evidence checks

def _contradictions(ev: BlackboardEvidence) -> List[str]:
    out: List[str] = []
    inc = ev.incident
    a, inv, sup, rt = ev.incident_assessment, ev.inventory_findings, ev.supplier_findings, ev.route_findings
    if a is not None:
        if a.incident_id != inc.incident_id:
            out.append("assessment belongs to a different incident")
        if not a.is_valid:
            out.append(f"incident failed Monitor validation: {a.validation_message}")
    if inv is not None:
        if (inv.warehouse_id, inv.product_id) != (inc.affected_warehouse_id, inc.affected_product_id):
            out.append("inventory_findings refer to a different warehouse/product")
        if inv.required_quantity != inc.required_quantity:
            out.append("inventory_findings required_quantity differs from the incident")
        if inv.usable_stock is None or inv.replenishment_required is None:
            out.append("inventory_findings lack usable_stock/replenishment_required")
        else:
            if inv.usable_stock != max(0, inv.available_stock - inv.safety_stock):
                out.append("inventory usable_stock is inconsistent with available/safety stock")
            if inv.replenishment_required != max(0, inv.required_quantity + inv.safety_stock - inv.available_stock):
                out.append("inventory replenishment_required is inconsistent")
    if sup is not None:
        if sup.product_id is not None and sup.product_id != inc.affected_product_id:
            out.append("supplier_findings refer to a different product")
        if sup.deadline_days is not None and sup.deadline_days != inc.deadline_days:
            out.append("supplier_findings deadline differs from the incident")
        if inc.original_supplier_id and inc.original_supplier_id not in sup.excluded_supplier_ids:
            out.append(f"supplier_findings did not exclude disrupted supplier {inc.original_supplier_id}")
    if rt is not None:
        if rt.destination_id is not None and rt.destination_id != inc.effective_destination:
            out.append("route_findings refer to a different destination")
        if rt.product_id is not None and rt.product_id != inc.affected_product_id:
            out.append("route_findings refer to a different product")
        if rt.required_quantity is not None and rt.required_quantity != inc.required_quantity:
            out.append("route_findings required_quantity differs from the incident")
        if inc.original_route_id and inc.original_route_id not in rt.blocked_route_ids:
            out.append(f"route_findings did not exclude blocked route {inc.original_route_id}")
        if inv is not None:
            known = {s.warehouse_id: s.usable_stock for s in inv.alternative_stock_locations}
            if inv.usable_stock is not None:
                known[inv.warehouse_id] = inv.usable_stock
            for ra in rt.route_assessments:
                if ra.origin in known and known[ra.origin] != ra.origin_usable_stock:
                    out.append(f"{ra.route_id}: origin stock {ra.origin_usable_stock} contradicts inventory "
                               f"evidence {known[ra.origin]} for {ra.origin}")
    return out


def read_blackboard_evidence(blackboard: "Blackboard") -> EvidenceReport:
    """Snapshot published Blackboard evidence and classify it (AVAILABLE/UNKNOWN/FAILED)."""
    from supply_chain.blackboard import Blackboard
    if not isinstance(blackboard, Blackboard):
        raise ToolInputError("read_blackboard_evidence requires the shared Blackboard")
    ev = blackboard.snapshot_evidence()
    status = ev.evidence_status()
    missing = [s for s in REQUIRED_EVIDENCE if status[s] == EvidenceStatus.UNKNOWN]
    failed = [s for s in REQUIRED_EVIDENCE if status[s] == EvidenceStatus.FAILED]
    contradictions = _contradictions(ev)
    return EvidenceReport(incident_id=ev.incident.incident_id, evidence=ev, evidence_status=status,
                          missing_sections=missing, failed_sections=failed, contradictions=contradictions,
                          sufficient_for_decision=not (missing or failed or contradictions))


# --------------------------------------------------------------------------- feasibility

def reorder_violations(supplier_id: str, ev: BlackboardEvidence) -> List[str]:
    inc, inv, sup = ev.incident, ev.inventory_findings, ev.supplier_findings
    v: List[str] = []
    if inc.disruption_type == DisruptionType.ROUTE_BLOCKAGE:
        v.append("REORDER does not resolve a route blockage")
    if inv is None or inv.replenishment_required is None or sup is None:
        return v + ["inventory or supplier evidence missing"]
    if inv.replenishment_required == 0:
        v.append("no replenishment required")
    if supplier_id in sup.excluded_supplier_ids or supplier_id == inc.original_supplier_id:
        v.append(f"{supplier_id} is the disrupted/excluded supplier")
    check = next((c for c in sup.capacity_checks if c.supplier_id == supplier_id), None)
    if check is None:
        return v + [f"no capacity evidence for {supplier_id}"]
    if not check.is_active:
        v.append(f"{supplier_id} is inactive")
    if not check.supports_product or check.product_id != inc.affected_product_id:
        v.append(f"{supplier_id} does not supply {inc.affected_product_id}")
    if check.available_capacity < inv.replenishment_required:
        v.append(f"{supplier_id} capacity {check.available_capacity} < replenishment "
                 f"{inv.replenishment_required}")
    if check.lead_time_days > inc.deadline_days:
        v.append(f"{supplier_id} lead time {check.lead_time_days}d > deadline {inc.deadline_days}d")
    return v


def reroute_violations(route_id: str, ev: BlackboardEvidence) -> List[str]:
    inc, rt = ev.incident, ev.route_findings
    if rt is None:
        return ["route evidence missing"]
    v: List[str] = []
    if route_id in rt.blocked_route_ids or route_id == inc.original_route_id:
        v.append(f"{route_id} is the reported blocked route")
    ra = next((a for a in rt.route_assessments if a.route_id == route_id), None)
    if ra is None:
        return v + [f"no assessment for {route_id}"]
    if ra.destination != inc.effective_destination:
        v.append(f"{route_id} ends at {ra.destination}, not {inc.effective_destination}")
    if ra.origin_usable_stock < inc.required_quantity:
        v.append(f"{route_id} origin {ra.origin} has {ra.origin_usable_stock} usable units < "
                 f"required {inc.required_quantity}")
    if ra.total_delivery_days > inc.deadline_days:
        v.append(f"{route_id} takes {ra.total_delivery_days}d > deadline {inc.deadline_days}d")
    return v


def feasible_options(ev: BlackboardEvidence) -> Dict[str, List[str]]:
    """Feasible supplier ids (fastest first) and route ids (fastest first) per the rules above."""
    sup, rt = ev.supplier_findings, ev.route_findings
    suppliers = sorted((c for c in (sup.capacity_checks if sup else []) if not reorder_violations(c.supplier_id, ev)),
                       key=lambda c: (c.lead_time_days, -c.available_capacity, c.supplier_id))
    routes = sorted((a for a in (rt.route_assessments if rt else []) if not reroute_violations(a.route_id, ev)),
                    key=lambda a: (a.total_delivery_days, a.route_id))
    return {"suppliers": [c.supplier_id for c in suppliers], "routes": [a.route_id for a in routes]}


def validate_mitigation_decision(decision: str, option_id: Optional[str],
                                 report: EvidenceReport) -> DecisionValidation:
    """Deterministic safety check of a proposed decision against Blackboard evidence."""
    try:
        d = MitigationDecision(decision)
    except ValueError:
        raise ToolInputError(f"decision must be one of {[m.value for m in MitigationDecision]}, got {decision!r}")
    if not isinstance(report, EvidenceReport):
        raise ToolInputError("validate_mitigation_decision requires an EvidenceReport")
    option_id = option_id or None
    ev = report.evidence
    v: List[str] = []
    opts = feasible_options(ev) if report.sufficient_for_decision else {"suppliers": [], "routes": []}
    if not report.sufficient_for_decision:
        if d != MitigationDecision.UNKNOWN:
            v.append("insufficient evidence: only UNKNOWN is allowed "
                     f"(missing={report.missing_sections}, failed={report.failed_sections}, "
                     f"contradictions={report.contradictions})")
    elif d == MitigationDecision.UNKNOWN:
        v.append("evidence is sufficient; UNKNOWN is not an acceptable outcome")
    elif d == MitigationDecision.REORDER:
        v += ["REORDER requires a supplier id"] if option_id is None else reorder_violations(option_id, ev)
    elif d == MitigationDecision.REROUTE:
        v += ["REROUTE requires a route id"] if option_id is None else reroute_violations(option_id, ev)
    elif d == MitigationDecision.HOLD:
        if opts["suppliers"] or opts["routes"]:
            v.append(f"HOLD rejected: feasible options exist {opts}")
    if d in (MitigationDecision.HOLD, MitigationDecision.UNKNOWN) and option_id is not None:
        v.append(f"{d.value} must not select an option")
    return DecisionValidation(decision=d, option_id=option_id, is_valid=not v, violations=v,
                              feasible_suppliers=opts["suppliers"], feasible_routes=opts["routes"])


def validate_citations(decision: str, option_id: Optional[str], report: EvidenceReport) -> tuple[bool, List[str]]:
    """Verify every cited ID actually exists on the blackboard.
    
    Returns: (all_valid, issues_found)
    """
    issues = []
    ev = report.evidence
    inc = ev.incident
    
    if decision == "REORDER" and option_id:
        # Option ID must be a supplier_id in supplier_findings.capacity_checks
        if not ev.supplier_findings:
            issues.append(f"REORDER cited supplier {option_id} but no supplier_findings on blackboard")
        else:
            supplier_ids = {c.supplier_id for c in ev.supplier_findings.capacity_checks}
            if option_id not in supplier_ids:
                issues.append(f"supplier {option_id} not in capacity_checks ({sorted(supplier_ids)})")
    
    elif decision == "REROUTE" and option_id:
        # Option ID must be a route_id in route_findings.route_assessments
        if not ev.route_findings:
            issues.append(f"REROUTE cited route {option_id} but no route_findings on blackboard")
        else:
            route_ids = {a.route_id for a in ev.route_findings.route_assessments}
            if option_id not in route_ids:
                issues.append(f"route {option_id} not in route_assessments ({sorted(route_ids)})")
    
    return len(issues) == 0, issues
