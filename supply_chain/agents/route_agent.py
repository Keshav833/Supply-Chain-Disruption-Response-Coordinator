"""Agent 4: Route Agent — alternative routes that can actually deliver.

Tools (SK plugin "RouteTools"): get_available_routes, calculate_route_delay.
Reads: incident, incident_assessment. Writes: route_findings.

A route is viable ONLY if its origin warehouse holds enough usable stock of the
product AND it delivers within the deadline. Speed alone never qualifies a route.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, Tuple

from semantic_kernel.functions import kernel_function

from supply_chain.agents.base import BaseAgent, ToolPlugin, require_valid_assessment
from supply_chain.contracts import AgentName, RouteAgentInput, RouteAgentOutput
from supply_chain.schemas import RouteAssessment, RouteFindings, SupplyChainDataset
from supply_chain.tools.common import RouteDelayReport, RouteSearch, ToolExecutionError
from supply_chain.tools.route_tools import calculate_route_delay, get_available_routes


class RoutePlugin(ToolPlugin):
    def __init__(self, agent, blackboard, dataset: Optional[SupplyChainDataset]):
        super().__init__(agent, blackboard)
        self.dataset = dataset

    @kernel_function(name="get_available_routes",
                     description="Available routes into a destination with usable stock at each route origin.")
    async def get_available_routes_fn(
            self, destination_id: Annotated[str, "Destination site id"], product_id: Annotated[str, "Product id"],
            required_quantity: Annotated[int, "Units required"],
            excluded_route_ids: Annotated[List[str], "Blocked route ids to exclude"]) -> str:
        return await self._run(
            "get_available_routes",
            lambda: get_available_routes(destination_id, product_id, required_quantity, excluded_route_ids,
                                         self.dataset),
            {"destination_id": destination_id, "product_id": product_id,
             "required_quantity": required_quantity, "excluded_route_ids": excluded_route_ids})

    @kernel_function(name="calculate_route_delay",
                     description="Transit + current delay and slack against the deadline for each route id.")
    async def calculate_route_delay_fn(self, route_ids: Annotated[List[str], "Route ids"],
                                       deadline_days: Annotated[int, "Deadline in days"]) -> str:
        return await self._run("calculate_route_delay",
                               lambda: calculate_route_delay(route_ids, deadline_days, self.dataset),
                               {"route_ids": route_ids, "deadline_days": deadline_days})


class RouteAgent(BaseAgent):
    name = AgentName.ROUTE
    plugin_name = "RouteTools"
    output_cls = RouteAgentOutput

    def __init__(self, interpreter: Any = None, dataset: Optional[SupplyChainDataset] = None):
        super().__init__(interpreter)
        self.dataset = dataset

    def make_plugin(self, blackboard):
        return RoutePlugin(self.name, blackboard, self.dataset)

    async def execute(self, kernel, blackboard) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        require_valid_assessment(blackboard)
        inc = blackboard.incident
        inp = RouteAgentInput(
            incident_id=inc.incident_id, product_id=inc.affected_product_id,
            affected_warehouse_id=inc.affected_warehouse_id, destination_id=inc.effective_destination,
            required_quantity=inc.required_quantity, deadline_days=inc.deadline_days,
            blocked_route_ids=[inc.original_route_id] if inc.original_route_id else [],
        )
        search: RouteSearch = await self.call_tool(
            kernel, "get_available_routes", RouteSearch, destination_id=inp.destination_id,
            product_id=inp.product_id, required_quantity=inp.required_quantity,
            excluded_route_ids=list(inp.blocked_route_ids))
        delays: RouteDelayReport = await self.call_tool(
            kernel, "calculate_route_delay", RouteDelayReport,
            route_ids=[c.route.route_id for c in search.candidates], deadline_days=inp.deadline_days)
        by_id = {d.route_id: d for d in delays.delays}
        assessments: List[RouteAssessment] = []
        for c in search.candidates:
            d = by_id.get(c.route.route_id)
            if d is None:
                raise ToolExecutionError(f"no delay result for {c.route.route_id}")
            problems = []
            if not c.stock_sufficient:
                problems.append(f"origin {c.route.origin} has {c.origin_usable_stock} usable < "
                                f"{inp.required_quantity} required")
            if not d.within_deadline:
                problems.append(f"{d.total_delivery_days}d > deadline {inp.deadline_days}d")
            assessments.append(RouteAssessment(
                route_id=c.route.route_id, origin=c.route.origin, destination=c.route.destination,
                origin_usable_stock=c.origin_usable_stock, required_quantity=inp.required_quantity,
                stock_sufficient=c.stock_sufficient, total_delivery_days=d.total_delivery_days,
                deadline_days=inp.deadline_days, within_deadline=d.within_deadline,
                viable=c.stock_sufficient and d.within_deadline,
                reason="; ".join(problems) or f"{c.origin_usable_stock} usable at origin, "
                                              f"{d.total_delivery_days}d <= {inp.deadline_days}d"))
        viable_ids = [a.route_id for a in assessments if a.viable]  # candidates already sorted fastest first
        routes = {c.route.route_id: c.route for c in search.candidates}
        best = routes[viable_ids[0]] if viable_ids else None
        if best:
            text = f"{best.route_id} from {best.origin} delivers in {best.total_delivery_days}d with stock available."
        elif assessments:
            text = "No viable route: " + "; ".join(f"{a.route_id}: {a.reason}" for a in assessments)
        else:
            text = f"No available alternative route into {inp.destination_id}."
        findings = RouteFindings(
            available_routes=[c.route for c in search.candidates], best_route=best,
            minimum_delivery_days=min((a.total_delivery_days for a in assessments), default=None),
            can_deliver_by_deadline=best is not None, recommendation=text, destination_id=inp.destination_id,
            blocked_route_ids=list(inp.blocked_route_ids), product_id=inp.product_id,
            required_quantity=inp.required_quantity, route_assessments=assessments,
        )
        return {"findings": findings}, {"summary": text}
