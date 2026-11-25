"""Route Agent tools: get_available_routes, calculate_route_delay.

A route is only a mitigation if goods exist at its START: get_available_routes
annotates every candidate with the usable stock of the requested product at the
route origin (no record = 0 usable units), so a fast route out of an empty
warehouse can never be reported as viable.
"""
from __future__ import annotations

from typing import List, Optional

from supply_chain.schemas import SupplyChainDataset
from supply_chain.tools.common import (
    PRODUCT,
    ROUTE,
    SITE,
    RejectedOption,
    RouteCandidate,
    RouteDelay,
    RouteDelayReport,
    RouteSearch,
    ToolInputError,
    dataset_or_default,
    require_id,
    require_ids,
    require_int,
)


def get_available_routes(destination_id: str, product_id: str, required_quantity: int,
                         excluded_route_ids: Optional[List[str]] = None,
                         dataset: Optional[SupplyChainDataset] = None) -> RouteSearch:
    """Available routes into destination_id, each annotated with origin stock sufficiency."""
    require_id("destination_id", destination_id, SITE)
    require_id("product_id", product_id, PRODUCT)
    require_int("required_quantity", required_quantity, 1)
    excluded = require_ids("excluded_route_ids", excluded_route_ids, ROUTE)
    ds = dataset_or_default(dataset)
    stock = {r.warehouse_id: r.usable_stock for r in ds.inventory if r.product_id == product_id}
    candidates, rejected = [], []
    for r in ds.routes:
        if r.destination != destination_id:
            continue
        if r.route_id in excluded:
            rejected.append(RejectedOption(option_id=r.route_id, reason="reported blocked/excluded route"))
        elif not r.is_available:
            rejected.append(RejectedOption(option_id=r.route_id, reason="route unavailable"))
        else:
            usable = stock.get(r.origin, 0)
            candidates.append(RouteCandidate(route=r, origin_usable_stock=usable,
                                             stock_sufficient=usable >= required_quantity))
    candidates.sort(key=lambda c: (c.route.total_delivery_days, -c.route.availability_score, c.route.route_id))
    return RouteSearch(destination_id=destination_id, product_id=product_id,
                       required_quantity=required_quantity, excluded_route_ids=excluded,
                       candidates=candidates, rejected=rejected)


def calculate_route_delay(route_ids: List[str], deadline_days: int,
                          dataset: Optional[SupplyChainDataset] = None) -> RouteDelayReport:
    """Total delivery time (transit + current delay) and slack against the deadline, per route."""
    ids = require_ids("route_ids", route_ids, ROUTE)
    if len(set(ids)) != len(ids):
        raise ToolInputError("route_ids contains duplicates")
    require_int("deadline_days", deadline_days, 1)
    ds = dataset_or_default(dataset)
    routes = {r.route_id: r for r in ds.routes}
    delays = []
    for rid in ids:
        r = routes.get(rid)
        if r is None:
            raise ToolInputError(f"unknown route {rid}")
        total = r.total_delivery_days
        delays.append(RouteDelay(route_id=rid, is_available=r.is_available,
                                 normal_transit_days=r.normal_transit_days,
                                 current_delay_days=r.current_delay_days, total_delivery_days=total,
                                 deadline_days=deadline_days, slack_days=deadline_days - total,
                                 within_deadline=r.is_available and total <= deadline_days))
    return RouteDelayReport(deadline_days=deadline_days, delays=delays)
