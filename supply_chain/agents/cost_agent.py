"""Cost Agent (Agent 6): Estimates and compares costs for mitigation options.

Tools (SK plugin "CostTools": estimate_option_cost, compare_options.
Reads: incident_assessment, inventory_findings, supplier_findings, route_findings.
Writes: cost_findings.
Runs concurrently with Inventory/Supplier/Route agents after Monitor validation.
"""
from __future__ import annotations
from typing import Annotated, Any, Dict, List, Optional, Tuple
from semantic_kernel.functions import kernel_function
from supply_chain.agents.base import BaseAgent, ToolPlugin, require_valid_assessment
from supply_chain.contracts import AgentName, CostAgentInput, CostAgentOutput
from supply_chain.schemas import CostEstimate, CostFindings, SupplyChainDataset
from supply_chain.tools.cost_tools import compare_options


class CostPlugin(ToolPlugin):
    def __init__(self, agent, blackboard, dataset: Optional[SupplyChainDataset]):
        super().__init__(agent, blackboard)
        self.dataset = dataset

    @kernel_function(name="estimate_option_cost",
                     description="Estimate cost for one mitigation option (REORDER or REROUTE).")
    async def estimate_option_cost_fn(
            self, decision_type: Annotated[str, "REORDER or REROUTE"],
            option_id: Annotated[str, "Supplier id or route id"],
            required_quantity: Annotated[int, "Units required"],
            deadline_days: Annotated[int, "Deadline in days"],
            lead_time_days: Annotated[int, "Lead time or transit time in days"]) -> str:
        from supply_chain.tools.cost_tools import estimate_option_cost
        return await self._run(
            "estimate_option_cost",
            lambda: estimate_option_cost(decision_type, option_id, required_quantity,
                                        deadline_days, lead_time_days, self.dataset),
            {"decision_type": decision_type, "option_id": option_id})

    @kernel_function(name="compare_options",
                     description="Compare costs of feasible mitigation options, identify cheapest.")
    async def compare_options_fn(
            self, options_json: Annotated[str, "JSON {option_id: [decision_type, lead_time, deadline, quantity]}"],
            required_quantity: Annotated[int, "Units required"],
            incident_id: Annotated[str, "Incident id"]) -> str:
        import json
        try:
            options = json.loads(options_json)
            options_tuples = {k: tuple(v) for k, v in options.items()}
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"Invalid options JSON: {e}")
        return await self._run(
            "compare_options",
            lambda: compare_options(options_tuples, required_quantity, incident_id, self.dataset),
            {"options": options, "required_quantity": required_quantity})


class CostAgent(BaseAgent):
    """Cost Agent: estimates and compares costs for viable options."""
    name = AgentName.COST
    plugin_name = "CostTools"
    output_cls = CostAgentOutput

    def __init__(self, interpreter: Any = None, dataset: Optional[SupplyChainDataset] = None):
        super().__init__(interpreter)
        self.dataset = dataset

    def make_plugin(self, blackboard):
        return CostPlugin(self.name, blackboard, self.dataset)

    async def execute(self, kernel, blackboard) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Execute Cost Agent: assess feasible options and estimate costs."""
        require_valid_assessment(blackboard)
        
        inc = blackboard.incident
        inv = blackboard.inventory_findings
        sup = blackboard.supplier_findings
        rt = blackboard.route_findings

        # REORDER replenishes safety stock too (replenishment_required); REROUTE ships incident demand.
        reorder_qty = (
            inv.replenishment_required
            if (inv and inv.replenishment_required is not None and inv.replenishment_required > 0)
            else inc.required_quantity
        )

        options: Dict[str, Tuple[str, int, int, int]] = {}
        
        if sup and sup.capacity_checks:
            for check in sup.capacity_checks:
                if check.feasible:
                    options[check.supplier_id] = ("REORDER", check.lead_time_days, inc.deadline_days, reorder_qty)
        
        if rt and rt.route_assessments:
            for assess in rt.route_assessments:
                if assess.viable:
                    options[assess.route_id] = ("REROUTE", assess.total_delivery_days, inc.deadline_days, inc.required_quantity)
        
        import json
        opts_json = json.dumps({k: list(v) for k, v in options.items()})
        
        # 1. First: Estimate cost for each viable option using estimate_option_cost
        estimates: List[CostEstimate] = []
        for option_id, (decision_type, lead_time, deadline, qty) in options.items():
            est: CostEstimate = await self.call_tool(
                kernel, "estimate_option_cost", CostEstimate,
                decision_type=decision_type,
                option_id=option_id,
                required_quantity=qty,
                deadline_days=deadline,
                lead_time_days=lead_time,
            )
            estimates.append(est)

        # If no viable options exist, still invoke estimate_option_cost on the first
        # candidate (informational only; compare_options still receives empty options)
        if not estimates:
            fallback_candidate = None
            if sup and sup.capacity_checks:
                c = sup.capacity_checks[0]
                fallback_candidate = ("REORDER", c.supplier_id, c.lead_time_days, inc.deadline_days, reorder_qty)
            elif rt and rt.route_assessments:
                a = rt.route_assessments[0]
                fallback_candidate = ("REROUTE", a.route_id, a.total_delivery_days, inc.deadline_days, inc.required_quantity)
            if fallback_candidate is not None:
                d_type, opt_id, lead_t, dl, qty = fallback_candidate
                await self.call_tool(
                    kernel, "estimate_option_cost", CostEstimate,
                    decision_type=d_type,
                    option_id=opt_id,
                    required_quantity=qty,
                    deadline_days=dl,
                    lead_time_days=lead_t,
                )

        # 2. Then: Compare viable options using compare_options
        findings: CostFindings = await self.call_tool(
            kernel, "compare_options", CostFindings,
            options_json=opts_json,
            required_quantity=inc.required_quantity,
            incident_id=inc.incident_id,
        )
        
        return ({"findings": findings}, {
            "recommendation": findings.recommendation,
            "cheapest_option": findings.cheapest_option,
        })

