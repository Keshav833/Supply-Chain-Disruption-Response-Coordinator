"""Agent 3: Supplier Agent — alternative suppliers, capacity and lead time.

Tools (SK plugin "SupplierTools"): find_alternative_suppliers, check_supplier_capacity.
Reads: incident, incident_assessment. Writes: supplier_findings.
Runs concurrently with Inventory, so capacity is checked against required_quantity;
the Coordinator re-checks it against inventory_findings.replenishment_required.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, Tuple

from semantic_kernel.functions import kernel_function

from supply_chain.agents.base import BaseAgent, ToolPlugin, require_valid_assessment
from supply_chain.contracts import AgentName, SupplierAgentInput, SupplierAgentOutput
from supply_chain.schemas import SupplierFindings, SupplyChainDataset
from supply_chain.tools.common import CapacityReport, SupplierSearch
from supply_chain.tools.supplier_tools import check_supplier_capacity, find_alternative_suppliers


class SupplierPlugin(ToolPlugin):
    def __init__(self, agent, blackboard, dataset: Optional[SupplyChainDataset]):
        super().__init__(agent, blackboard)
        self.dataset = dataset

    @kernel_function(name="find_alternative_suppliers",
                     description="Active suppliers of a product, excluding disrupted supplier ids.")
    async def find_alternative_suppliers_fn(
            self, product_id: Annotated[str, "Product id"],
            excluded_supplier_ids: Annotated[List[str], "Supplier ids to exclude"]) -> str:
        return await self._run("find_alternative_suppliers",
                               lambda: find_alternative_suppliers(product_id, excluded_supplier_ids, self.dataset),
                               {"product_id": product_id, "excluded_supplier_ids": excluded_supplier_ids})

    @kernel_function(name="check_supplier_capacity",
                     description="Check status, capacity and lead time of suppliers for a quantity and deadline.")
    async def check_supplier_capacity_fn(
            self, supplier_ids: Annotated[List[str], "Supplier ids to check"],
            product_id: Annotated[str, "Product id"], required_quantity: Annotated[int, "Units required"],
            deadline_days: Annotated[int, "Deadline in days"]) -> str:
        return await self._run(
            "check_supplier_capacity",
            lambda: check_supplier_capacity(supplier_ids, product_id, required_quantity, deadline_days,
                                            self.dataset),
            {"supplier_ids": supplier_ids, "product_id": product_id,
             "required_quantity": required_quantity, "deadline_days": deadline_days})


class SupplierAgent(BaseAgent):
    name = AgentName.SUPPLIER
    plugin_name = "SupplierTools"
    output_cls = SupplierAgentOutput

    def __init__(self, interpreter: Any = None, dataset: Optional[SupplyChainDataset] = None):
        super().__init__(interpreter)
        self.dataset = dataset

    def make_plugin(self, blackboard):
        return SupplierPlugin(self.name, blackboard, self.dataset)

    async def execute(self, kernel, blackboard) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        require_valid_assessment(blackboard)
        inc = blackboard.incident
        inp = SupplierAgentInput(
            incident_id=inc.incident_id, product_id=inc.affected_product_id,
            warehouse_id=inc.affected_warehouse_id, required_quantity=inc.required_quantity,
            deadline_days=inc.deadline_days,
            excluded_supplier_ids=[inc.original_supplier_id] if inc.original_supplier_id else [],
        )
        search: SupplierSearch = await self.call_tool(
            kernel, "find_alternative_suppliers", SupplierSearch, product_id=inp.product_id,
            excluded_supplier_ids=list(inp.excluded_supplier_ids))
        # Candidates plus rejected suppliers, so the evidence documents why each was ruled out.
        to_check = [s.supplier_id for s in search.candidates] + [r.option_id for r in search.rejected]
        report: CapacityReport = await self.call_tool(
            kernel, "check_supplier_capacity", CapacityReport, supplier_ids=to_check,
            product_id=inp.product_id, required_quantity=inp.required_quantity, deadline_days=inp.deadline_days)
        feasible = {c.supplier_id for c in report.checks if c.feasible}
        viable = [s for s in search.candidates if s.supplier_id in feasible]
        best = viable[0] if viable else None
        if best:
            text = (f"{best.supplier_id} can supply {inp.required_quantity} units in {best.lead_time_days}d "
                    f"(capacity {best.available_capacity}).")
        else:
            text = "No alternative supplier meets capacity and deadline: " + "; ".join(
                f"{c.supplier_id}: {c.reason}" for c in report.checks)
        findings = SupplierFindings(
            viable_suppliers=viable, best_supplier=best, can_fulfill_by_deadline=best is not None,
            recommendation=text, excluded_supplier_ids=list(inp.excluded_supplier_ids),
            deadline_days=inp.deadline_days, product_id=inp.product_id, required_quantity=inp.required_quantity,
            capacity_checks=report.checks,
        )
        return {"findings": findings}, {"summary": text}
