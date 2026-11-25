"""Agent 5: Coordinator Agent — decision from Blackboard evidence ONLY.

Tools (SK plugin "CoordinatorTools"): read_blackboard_evidence, validate_mitigation_decision.
Reads: published Blackboard evidence. Writes: mitigation_decision (+ notifications).

This module deliberately has no access to the raw datasets: it receives only the
shared Blackboard. Missing/failed/contradictory evidence yields UNKNOWN, never a
guessed mitigation. Every proposed decision is re-validated before publishing.
"""
from __future__ import annotations

import json
import logging

from typing import Annotated, Any, Dict, List, Optional, Tuple

from semantic_kernel.functions import kernel_function

from supply_chain.agents.base import BaseAgent, ToolPlugin
from supply_chain.contracts import AgentName, CoordinatorAgentInput, CoordinatorAgentOutput
from supply_chain.schemas import DisruptionType, MitigationDecision, MitigationResult, Notification
from supply_chain.tools.coordinator_tools import (
    validate_citations,
    DecisionValidation,
    EvidenceReport,
    feasible_options,
    read_blackboard_evidence,
    validate_mitigation_decision,
)


class CoordinatorPlugin(ToolPlugin):
    def __init__(self, agent, blackboard):
        super().__init__(agent, blackboard)
        self._report: Optional[EvidenceReport] = None

    @kernel_function(name="propose_decision",
                     description="Propose a mitigation decision (REORDER/REROUTE/HOLD/UNKNOWN) with justification. Return JSON: {{decision, selected_option_id, justification}}.")
    async def propose_decision_fn(self) -> str:
        def run():
            report = self._report or read_blackboard_evidence(self.blackboard)
            # Return evidence summary for LLM to propose on
            from supply_chain.tools.coordinator_tools import feasible_options
            opts = feasible_options(report.evidence)
            return {
                "recommendation": "Propose a decision based on evidence",
                "feasible_suppliers": opts["suppliers"],
                "feasible_routes": opts["routes"],
                "inventory": report.evidence.inventory_findings.recommendation if report.evidence.inventory_findings else None,
                "suppliers": report.evidence.supplier_findings.recommendation if report.evidence.supplier_findings else None,
                "routes": report.evidence.route_findings.recommendation if report.evidence.route_findings else None,
            }
        return await self._run("propose_decision", run, {})

    @kernel_function(name="read_blackboard_evidence",
                     description="Read published Blackboard evidence with AVAILABLE/UNKNOWN/FAILED status.")
    async def read_blackboard_evidence_fn(self) -> str:
        def run():
            self._report = read_blackboard_evidence(self.blackboard)
            return self._report
        return await self._run("read_blackboard_evidence", run, {})

    @kernel_function(name="validate_mitigation_decision",
                     description="Validate a proposed REORDER/REROUTE/HOLD/UNKNOWN decision against evidence.")
    async def validate_mitigation_decision_fn(
            self, decision: Annotated[str, "REORDER, REROUTE, HOLD or UNKNOWN"],
            option_id: Annotated[str, "Supplier id (REORDER) or route id (REROUTE); empty otherwise"] = "") -> str:
        def run():
            report = self._report or read_blackboard_evidence(self.blackboard)
            return validate_mitigation_decision(decision, option_id or None, report)
        return await self._run("validate_mitigation_decision", run,
                               {"decision": decision, "option_id": option_id})


def propose_decision(report: EvidenceReport) -> Tuple[MitigationDecision, Optional[str], str]:
    """Deterministic decision policy over Blackboard evidence."""
    ev = report.evidence
    inc = ev.incident
    if not report.sufficient_for_decision:
        reasons = []
        if report.missing_sections:
            reasons.append(f"UNKNOWN evidence: {report.missing_sections}")
        if report.failed_sections:
            reasons.append(f"FAILED evidence: {report.failed_sections}")
        if report.contradictions:
            reasons.append(f"contradictions: {report.contradictions}")
        return MitigationDecision.UNKNOWN, None, "No decision: " + "; ".join(reasons) + "."
    opts = feasible_options(ev)
    inv, sup, rt = ev.inventory_findings, ev.supplier_findings, ev.route_findings
    reorder = None
    if opts["suppliers"]:
        sid = opts["suppliers"][0]
        c = next(c for c in sup.capacity_checks if c.supplier_id == sid)
        reorder = (MitigationDecision.REORDER, sid,
                   f"REORDER from {sid}: capacity {c.available_capacity} >= replenishment "
                   f"{inv.replenishment_required} and lead time {c.lead_time_days}d <= {inc.deadline_days}d.")
    reroute = None
    if opts["routes"]:
        rid = opts["routes"][0]
        a = next(a for a in rt.route_assessments if a.route_id == rid)
        reroute = (MitigationDecision.REROUTE, rid,
                   f"REROUTE via {rid} from {a.origin}: {a.origin_usable_stock} usable units >= "
                   f"{inc.required_quantity} required and {a.total_delivery_days}d <= {inc.deadline_days}d.")
    ordered = [reroute, reorder] if inc.disruption_type == DisruptionType.ROUTE_BLOCKAGE else [reorder, reroute]
    for choice in ordered:
        if choice:
            return choice
    why = [f"Supplier: {sup.recommendation.rstrip('.')}.", f"Route: {rt.recommendation.rstrip('.')}."]
    if inc.disruption_type != DisruptionType.ROUTE_BLOCKAGE and inv.replenishment_required:
        why.insert(0, f"Replenishment needed: {inv.replenishment_required} units.")
    if inc.disruption_type == DisruptionType.ROUTE_BLOCKAGE:
        why.insert(0, "REORDER does not resolve a route blockage.")
    return MitigationDecision.HOLD, None, "HOLD: no feasible mitigation. " + " ".join(why)


def notifications_for(decision: MitigationDecision, option: Optional[str], report: EvidenceReport) -> List[Notification]:
    inc = report.evidence.incident
    n = [Notification(party="Operations Manager", reason=f"{decision.value} recommended for {inc.incident_id}; "
                                                        "human authorization required")]
    if decision == MitigationDecision.REORDER:
        n += [Notification(party="Procurement Team", reason=f"Prepare purchase order with {option}"),
              Notification(party=f"Supplier {option}", reason=f"Request {inc.affected_product_id} quote/confirmation"),
              Notification(party=f"Warehouse {inc.affected_warehouse_id}", reason="Expect replenishment")]
    elif decision == MitigationDecision.REROUTE:
        a = next(a for a in report.evidence.route_findings.route_assessments if a.route_id == option)
        n += [Notification(party="Logistics Team", reason=f"Book shipment via {option}"),
              Notification(party=f"Warehouse {a.origin}", reason=f"Pick {inc.required_quantity} {inc.affected_product_id}"),
              Notification(party=f"Destination {inc.effective_destination}", reason="Revised delivery plan")]
    elif decision == MitigationDecision.HOLD:
        n += [Notification(party=f"Warehouse {inc.affected_warehouse_id}", reason="No feasible mitigation; "
                                                                               "escalate and monitor")]
    else:
        n += [Notification(party="Supply Chain Data Owners", reason="Evidence missing/failed/contradictory: "
                                                                    "investigate before deciding")]
    return n


class CoordinatorAgent(BaseAgent):
    name = AgentName.COORDINATOR
    plugin_name = "CoordinatorTools"
    output_cls = CoordinatorAgentOutput

    def make_plugin(self, blackboard):
        return CoordinatorPlugin(self.name, blackboard)

    async def execute(self, kernel, blackboard) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Execute Coordinator with agentic LLM proposal + safety guardrails."""
        report: EvidenceReport = await self.call_tool(kernel, "read_blackboard_evidence", EvidenceReport)
        inp = CoordinatorAgentInput(incident_id=report.incident_id, evidence=report.evidence)
        
        llm_proposal = None
        proposed_by = "deterministic"
        guardrail_check = None
        
        is_live_llm = (self.interpreter and 
                      type(self.interpreter).__name__ == "SemanticKernelInterpreter" and 
                      hasattr(self.interpreter, 'live') and
                      self.interpreter.live)
        
        if is_live_llm:
            try:
                proposal_json_str: str = await self.call_tool(kernel, "propose_decision", str)
                proposal = json.loads(proposal_json_str)
                llm_decision_str = proposal.get("decision", "UNKNOWN")
                llm_option = proposal.get("selected_option_id")
                llm_justification = proposal.get("justification", "")
                
                try:
                    llm_decision = MitigationDecision(llm_decision_str)
                except ValueError:
                    raise ValueError(f"invalid decision: {llm_decision_str}")
                
                check: DecisionValidation = await self.call_tool(
                    kernel, "validate_mitigation_decision", DecisionValidation,
                    decision=llm_decision.value, option_id=llm_option or "")
                
                if check.is_valid:
                    citations_valid, citation_issues = validate_citations(llm_decision.value, llm_option, report)
                    if citations_valid:
                        llm_proposal = (llm_decision, llm_option, llm_justification)
                        proposed_by = "llm"
                        guardrail_check = {"accepted": True, "reason": "LLM proposal passed validation and citation checks"}
                    else:
                        guardrail_check = {"accepted": False, "reason": "citation validation failed", "issues": citation_issues}
                else:
                    guardrail_check = {"accepted": False, "reason": "decision validation failed", "violations": check.violations}
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.warning("LLM proposal failed: %s; falling back to deterministic", e)
                guardrail_check = {"accepted": False, "reason": f"LLM proposal error: {type(e).__name__}"}
        
        if llm_proposal:
            decision, option, justification = llm_proposal
        else:
            decision, option, justification = propose_decision(report)
        
        check: DecisionValidation = await self.call_tool(
            kernel, "validate_mitigation_decision", DecisionValidation,
            decision=decision.value, option_id=option or "")
        if not check.is_valid:
            raise RuntimeError(f"final validation failed: {check.violations}")
        
        result = MitigationResult(
            incident_id=inp.incident_id, decision=decision, justification=justification,
            parties_to_notify=[x.party for x in notifications_for(decision, option, report)],
            selected_option_id=option,
            evidence_summary={
                "evidence_status": {k: v.value for k, v in report.evidence_status.items()},
                "missing_sections": report.missing_sections, "failed_sections": report.failed_sections,
                "contradictions": report.contradictions,
                "feasible_suppliers": check.feasible_suppliers, "feasible_routes": check.feasible_routes,
            },
            proposed_by=proposed_by,
            guardrail_check=guardrail_check,
        )
        notes = notifications_for(decision, option, report)
        return ({"decision": result, "notifications": notes},
                {"summary": justification, "decision": decision.value, "option": option})
