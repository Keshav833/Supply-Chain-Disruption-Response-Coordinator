"""Agent-level test harness: runs the five agents on one shared Blackboard WITHOUT the
orchestrator's gates (e.g. specialists still run after a failed validation), so agent
behaviour can be tested in isolation. The production workflow is supply_chain.orchestrator.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from supply_chain.agents.coordinator_agent import CoordinatorAgent
from supply_chain.agents.inventory_agent import InventoryAgent
from supply_chain.agents.monitor_agent import MonitorAgent
from supply_chain.agents.route_agent import RouteAgent
from supply_chain.agents.supplier_agent import SupplierAgent
from supply_chain.blackboard import Blackboard
from supply_chain.contracts import AgentName
from supply_chain.schemas import DisruptionIncident


async def run_pipeline(incident: DisruptionIncident, dataset=None, interpreter=None,
                       skip: tuple = ()) -> Dict[str, Any]:
    bb = Blackboard(incident)
    outputs: Dict[AgentName, Any] = {}
    outputs[AgentName.MONITOR] = await MonitorAgent(interpreter, dataset).run(bb)
    specialists = [a for a in (InventoryAgent(interpreter, dataset), SupplierAgent(interpreter, dataset),
                               RouteAgent(interpreter, dataset)) if a.name not in skip]
    for agent, out in zip(specialists, await asyncio.gather(*(a.run(bb) for a in specialists))):
        outputs[agent.name] = out
    outputs[AgentName.COORDINATOR] = await CoordinatorAgent(interpreter).run(bb)
    return {"blackboard": bb, "outputs": outputs, "coordinator": outputs[AgentName.COORDINATOR]}
