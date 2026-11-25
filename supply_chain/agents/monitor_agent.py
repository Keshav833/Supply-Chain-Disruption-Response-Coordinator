"""Agent 1: Monitor Agent — validates the report and identifies affected sites.

Tools (SK plugin "MonitorTools"): validate_disruption, identify_affected_sites.
Writes: incident_assessment.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, Optional, Tuple

from semantic_kernel.functions import kernel_function

from supply_chain.agents.base import BaseAgent, ToolPlugin
from supply_chain.contracts import AgentName, MonitorAgentInput, MonitorAgentOutput
from supply_chain.schemas import DisruptionIncident, IncidentAssessment, SupplyChainDataset
from supply_chain.tools.common import AffectedSites, DisruptionValidation
from supply_chain.tools.monitor_tools import identify_affected_sites, validate_disruption


class MonitorPlugin(ToolPlugin):
    def __init__(self, agent, blackboard, dataset: Optional[SupplyChainDataset]):
        super().__init__(agent, blackboard)
        self.dataset = dataset

    @kernel_function(name="validate_disruption",
                     description="Validate a disruption report (JSON) against master and network data.")
    async def validate_disruption_fn(
            self, incident_json: Annotated[str, "Disruption report as JSON"]) -> Annotated[str, "Validation JSON"]:
        import json
        return await self._run("validate_disruption",
                               lambda: validate_disruption(json.loads(incident_json), self.dataset),
                               {"incident_json": incident_json})

    @kernel_function(name="identify_affected_sites",
                     description="List affected sites, the blocked entity and related suppliers/routes.")
    async def identify_affected_sites_fn(
            self, incident_json: Annotated[str, "Validated report as JSON"]) -> Annotated[str, "Sites JSON"]:
        return await self._run(
            "identify_affected_sites",
            lambda: identify_affected_sites(DisruptionIncident.model_validate_json(incident_json), self.dataset),
            {"incident_json": incident_json})


class MonitorAgent(BaseAgent):
    name = AgentName.MONITOR
    plugin_name = "MonitorTools"
    output_cls = MonitorAgentOutput

    def __init__(self, interpreter: Any = None, dataset: Optional[SupplyChainDataset] = None):
        super().__init__(interpreter)
        self.dataset = dataset

    def make_plugin(self, blackboard):
        return MonitorPlugin(self.name, blackboard, self.dataset)

    async def execute(self, kernel, blackboard) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        inp = MonitorAgentInput(incident_id=blackboard.incident_id, incident=blackboard.incident)
        report = inp.incident.model_dump_json()
        validation: DisruptionValidation = await self.call_tool(
            kernel, "validate_disruption", DisruptionValidation, incident_json=report)
        sites: AffectedSites = await self.call_tool(
            kernel, "identify_affected_sites", AffectedSites, incident_json=report)
        assessment = IncidentAssessment(
            incident_id=inp.incident_id, is_valid=validation.is_valid,
            validation_message=validation.validation_message,
            affected_sites=sites.affected_sites if validation.is_valid else [],
            severity=validation.severity, requires_immediate_action=validation.requires_immediate_action,
        )
        facts = {"summary": f"{validation.validation_message} Affected sites: {sites.affected_sites}.",
                 "blocked_entity": sites.blocked_entity}
        return {"assessment": assessment}, facts
