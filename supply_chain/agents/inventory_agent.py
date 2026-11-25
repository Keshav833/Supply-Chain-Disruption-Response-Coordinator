"""Agent 2: Inventory Agent — stock position and replenishment need.

Tools (SK plugin "InventoryTools"): get_inventory, calculate_stock_shortage.
Reads: incident, incident_assessment. Writes: inventory_findings.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, Optional, Tuple

from semantic_kernel.functions import kernel_function

from supply_chain.agents.base import BaseAgent, ToolPlugin, require_valid_assessment
from supply_chain.contracts import AgentName, InventoryAgentInput, InventoryAgentOutput
from supply_chain.schemas import InventoryFindings, SupplyChainDataset
from supply_chain.tools.common import InventoryLookup, StockShortage, ToolExecutionError
from supply_chain.tools.inventory_tools import calculate_stock_shortage, get_inventory


class InventoryPlugin(ToolPlugin):
    def __init__(self, agent, blackboard, dataset: Optional[SupplyChainDataset]):
        super().__init__(agent, blackboard)
        self.dataset = dataset

    @kernel_function(name="get_inventory",
                     description="Stock records for a product at one warehouse, or all warehouses if empty.")
    async def get_inventory_fn(self, product_id: Annotated[str, "Product id, e.g. LAPTOP-001"],
                               warehouse_id: Annotated[str, "Warehouse id, or empty for all"] = "") -> str:
        return await self._run("get_inventory",
                               lambda: get_inventory(product_id, warehouse_id or None, self.dataset),
                               {"product_id": product_id, "warehouse_id": warehouse_id})

    @kernel_function(name="calculate_stock_shortage",
                     description="Usable stock, shortage and safety-aware replenishment requirement.")
    async def calculate_stock_shortage_fn(self, available_stock: Annotated[int, "Units on hand"],
                                          safety_stock: Annotated[int, "Safety stock units"],
                                          required_quantity: Annotated[int, "Units required"]) -> str:
        return await self._run("calculate_stock_shortage",
                               lambda: calculate_stock_shortage(available_stock, safety_stock, required_quantity),
                               {"available_stock": available_stock, "safety_stock": safety_stock,
                                "required_quantity": required_quantity})


class InventoryAgent(BaseAgent):
    name = AgentName.INVENTORY
    plugin_name = "InventoryTools"
    output_cls = InventoryAgentOutput

    def __init__(self, interpreter: Any = None, dataset: Optional[SupplyChainDataset] = None):
        super().__init__(interpreter)
        self.dataset = dataset

    def make_plugin(self, blackboard):
        return InventoryPlugin(self.name, blackboard, self.dataset)

    async def execute(self, kernel, blackboard) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        require_valid_assessment(blackboard)
        inc = blackboard.incident
        inp = InventoryAgentInput(incident_id=inc.incident_id, product_id=inc.affected_product_id,
                                  warehouse_id=inc.affected_warehouse_id, required_quantity=inc.required_quantity)
        local: InventoryLookup = await self.call_tool(kernel, "get_inventory", InventoryLookup,
                                                      product_id=inp.product_id, warehouse_id=inp.warehouse_id)
        if len(local.records) != 1:
            raise ToolExecutionError(f"no inventory record for {inp.product_id} at {inp.warehouse_id}; "
                                     "stock is UNKNOWN, not zero")
        rec = local.records[0]
        network: InventoryLookup = await self.call_tool(kernel, "get_inventory", InventoryLookup,
                                                        product_id=inp.product_id, warehouse_id="")
        calc: StockShortage = await self.call_tool(
            kernel, "calculate_stock_shortage", StockShortage, available_stock=rec.available_stock,
            safety_stock=rec.safety_stock, required_quantity=inp.required_quantity)
        alternatives = [s for s in network.records if s.warehouse_id != inp.warehouse_id]
        if calc.can_fulfil_from_stock:
            rec_text = f"Local usable stock {calc.usable_stock} covers the {inp.required_quantity} required."
        else:
            rec_text = (f"Shortfall: usable {calc.usable_stock} < required {inp.required_quantity}; "
                        f"replenish {calc.replenishment_required} units to also restore safety stock.")
        findings = InventoryFindings(
            warehouse_id=inp.warehouse_id, product_id=inp.product_id, available_stock=rec.available_stock,
            safety_stock=rec.safety_stock, required_quantity=inp.required_quantity, shortage=calc.shortage,
            safety_stock_met=calc.safety_stock_met, recommendation=rec_text, usable_stock=calc.usable_stock,
            replenishment_required=calc.replenishment_required, alternative_stock_locations=alternatives,
        )
        return {"findings": findings}, {"summary": rec_text}
