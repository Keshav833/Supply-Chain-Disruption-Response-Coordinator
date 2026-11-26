"Cost Agent tools: estimate_option_cost, compare_options."
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from supply_chain.schemas import CostEstimate, CostFindings, SupplyChainDataset
from supply_chain.tools.common import ToolInputError, dataset_or_default


def estimate_option_cost(
    decision_type: str, option_id: str, required_quantity: int,
    deadline_days: int, lead_time_or_transit_days: int,
    dataset: Optional[SupplyChainDataset] = None,
) -> CostEstimate:
    "Estimate cost for one mitigation option (REORDER or REROUTE)."
    if decision_type not in ("REORDER", "REROUTE"):
        raise ToolInputError(f"decision_type must be REORDER or REROUTE, got {decision_type}")
    
    ds = dataset_or_default(dataset)
    cost_per_unit = 0.0
    cost_category = ""
    
    if decision_type == "REORDER":
        supplier = next((s for s in ds.suppliers if s.supplier_id == option_id), None)
        if not supplier:
            raise ToolInputError(f"supplier {option_id} not found")
        cost_per_unit = getattr(supplier, "unit_cost", 500.0)
        cost_category = "procurement"
    else:
        route = next((r for r in ds.routes if r.route_id == option_id), None)
        if not route:
            raise ToolInputError(f"route {option_id} not found")
        cost_per_unit = getattr(route, "cost_per_unit", 20.0)
        cost_category = "logistics"
    
    total_cost = cost_per_unit * required_quantity
    breakdown = {"base_cost": total_cost}
    
    expedited_threshold = 3
    if lead_time_or_transit_days <= expedited_threshold and lead_time_or_transit_days < deadline_days:
        if decision_type == "REORDER":
            expedited_pct = 0.15
        else:
            expedited_pct = getattr(next(r for r in ds.routes if r.route_id == option_id), "expedited_fee_percent", 0.10)
        expedited_fee = total_cost * expedited_pct
        breakdown["expedited_fee"] = expedited_fee
        total_cost += expedited_fee
    
    return CostEstimate(
        option_type=decision_type, option_id=option_id,
        cost_per_unit=cost_per_unit, total_cost=total_cost,
        cost_category=cost_category, breakdown=breakdown,
    )


def compare_options(
    options: Dict[str, Tuple[Any, ...]],
    required_quantity: int, incident_id: str,
    dataset: Optional[SupplyChainDataset] = None,
) -> CostFindings:
    "Compare costs of multiple viable options, return cheapest."
    if not options:
        return CostFindings(
            incident_id=incident_id, cost_estimates=[],
            cheapest_option=None, cost_comparison={},
            recommendation="No viable options to compare.",
        )
    
    estimates: List[CostEstimate] = []
    cost_comparison: Dict[str, float] = {}
    
    for option_id, spec in options.items():
        decision_type, lead_time, deadline = spec[0], spec[1], spec[2]
        qty = spec[3] if len(spec) > 3 else required_quantity
        try:
            est = estimate_option_cost(
                decision_type, option_id, qty, deadline, lead_time, dataset
            )
            estimates.append(est)
            cost_comparison[option_id] = est.total_cost
        except ToolInputError:
            continue
    
    if estimates:
        estimates.sort(key=lambda e: e.total_cost)
        cheapest = estimates[0]
        rec = f"Cheapest option is {cheapest.option_id} ({cheapest.option_type}) at ${cheapest.total_cost:.2f}."
        return CostFindings(
            incident_id=incident_id, cost_estimates=estimates,
            cheapest_option=cheapest.option_id, cost_comparison=cost_comparison,
            recommendation=rec,
        )
    
    return CostFindings(
        incident_id=incident_id, cost_estimates=[],
        cheapest_option=None, cost_comparison=cost_comparison,
        recommendation="No costs could be estimated.",
    )
