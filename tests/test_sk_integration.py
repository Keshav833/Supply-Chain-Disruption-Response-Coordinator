"""Semantic Kernel 1.47.0 integration: plugin registration and REAL tool invocation.

All tests here are OFFLINE. They use the scripted test service in tests/sk_fakes.py,
not Gemini. The only live test is skipped unless a real GEMINI_API_KEY is configured.
"""
import json

import pytest
from semantic_kernel import Kernel
from semantic_kernel.agents import ChatCompletionAgent
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.filters import FilterTypes

from supply_chain import config
from supply_chain.agents.coordinator_agent import CoordinatorAgent
from supply_chain.agents.cost_agent import CostAgent
from supply_chain.agents.inventory_agent import InventoryAgent
from supply_chain.agents.monitor_agent import MonitorAgent
from supply_chain.agents.route_agent import RouteAgent
from supply_chain.agents.supplier_agent import SupplierAgent
from supply_chain.blackboard import Blackboard
from supply_chain.contracts import REQUIRED_TOOLS, AgentName
from supply_chain.data.sample_data import SAMPLE_INCIDENTS
from supply_chain.llm.interpreter import SemanticKernelInterpreter
from supply_chain.schemas import DisruptionIncident, ExecutionMode
from tests.helpers import run_pipeline
from tests.sk_fakes import ScriptedTextService, ScriptedToolCallingService

AGENTS = [MonitorAgent, InventoryAgent, SupplierAgent, RouteAgent, CostAgent, CoordinatorAgent]


def inc(inc_id):
    return DisruptionIncident(**next(d for d in SAMPLE_INCIDENTS if d["incident_id"] == inc_id))


@pytest.mark.parametrize("agent_cls", AGENTS)
def test_each_agent_registers_its_two_tools_as_sk_plugin(agent_cls):
    agent = agent_cls()
    kernel = agent.build_kernel(Blackboard(inc("INC-1001")))
    assert isinstance(kernel, Kernel)
    plugin = kernel.get_plugin(agent.plugin_name)
    assert set(plugin.functions) == set(REQUIRED_TOOLS[agent.name])
    for fn in plugin.functions.values():
        assert fn.description and fn.metadata.plugin_name == agent.plugin_name


async def test_agent_tool_calls_go_through_the_sk_function_pipeline(monkeypatch):
    """A kernel-level function-invocation filter sees every tool call made by every agent."""
    seen = []
    original = Kernel.__init__

    def patched(self, *a, **kw):
        original(self, *a, **kw)

        async def spy(context, next):  # SK requires the name `next`
            await next(context)
            seen.append(context.function.fully_qualified_name)
        self.add_filter(FilterTypes.FUNCTION_INVOCATION, spy)

    monkeypatch.setattr(Kernel, "__init__", patched)
    await run_pipeline(inc("INC-1001"))
    for agent in AGENTS:
        for tool in REQUIRED_TOOLS[agent.name]:
            assert f"{agent.plugin_name}-{tool}" in seen


async def test_offline_function_calling_service_invokes_real_route_tools():
    """SK's auto function-calling loop executes the registered Python tools (offline service)."""
    bb = Blackboard(inc("INC-1005"))
    agent = RouteAgent()
    await bb.mark_running(AgentName.ROUTE)
    kernel = agent.build_kernel(bb)
    service = ScriptedToolCallingService(script=[
        [{"plugin": "RouteTools", "function": "get_available_routes",
          "arguments": {"destination_id": "CUST-MUM", "product_id": "LAPTOP-001", "required_quantity": 30,
                        "excluded_route_ids": ["RT-PUN-MUM-01"]}}],
        [{"plugin": "RouteTools", "function": "calculate_route_delay",
          "arguments": {"route_ids": ["RT-MUM-MUM-02", "RT-DEL-MUM-03"], "deadline_days": 5}}],
    ])
    sk_agent = ChatCompletionAgent(service=service, kernel=kernel, name="RouteAgent",
                                   instructions="Use the tools.",
                                   function_choice_behavior=FunctionChoiceBehavior.Auto())
    response = await sk_agent.get_response(messages="Find a route for INC-1005")
    assert "[offline test service]" in str(response.content)
    assert bb.tools_invoked["RouteAgent"] == ["get_available_routes", "calculate_route_delay"]
    results = [json.loads(str(r.result)) for r in service.seen_results]
    assert [c["route"]["route_id"] for c in results[0]["candidates"]] == ["RT-MUM-MUM-02", "RT-DEL-MUM-03"]
    assert [d["total_delivery_days"] for d in results[1]["delays"]] == [1, 4]
    assert all(e.execution_mode is None for e in bb.run_log)  # no LLM claim recorded


async def test_interpreter_labels_offline_service_as_fallback_and_cannot_change_decision():
    interp = SemanticKernelInterpreter(ScriptedTextService(text="Decision: REROUTE via RT-X"), live=False)
    res = await run_pipeline(inc("INC-1004"), interpreter=interp)
    c = res["coordinator"]
    assert c.decision.decision.value == "HOLD" and c.decision.selected_option_id is None
    assert c.interpretation == "Decision: REROUTE via RT-X"
    assert c.execution_mode == ExecutionMode.DETERMINISTIC_FALLBACK
    assert all(o.execution_mode == ExecutionMode.DETERMINISTIC_FALLBACK for o in res["outputs"].values())


async def test_interpreter_failure_falls_back_with_label():
    interp = SemanticKernelInterpreter(ScriptedTextService(fail=True), live=True)
    out = await interp.interpret("MonitorAgent", {"summary": "x"})
    assert out.mode == ExecutionMode.DETERMINISTIC_FALLBACK and "ConnectionError" in out.error
    assert out.text.startswith("[deterministic_fallback]")


@pytest.mark.skipif(not config.GEMINI_API_KEY, reason="no GEMINI_API_KEY: live LLM test skipped")
async def test_live_gemini_function_calling():  # pragma: no cover - requires network + key
    """
    Test live Gemini function calling via SK.
    
    Skips if:
      - no GEMINI_API_KEY (needs valid credentials)
      - network unreachable (DNS lookup fails; common in sandboxed/offline environments)
    """
    import socket
    from semantic_kernel.connectors.ai.google.google_ai import GoogleAIChatCompletion
    
    # Quick check: can we reach Google's API?
    try:
        socket.getaddrinfo("generativelanguage.googleapis.com", 443, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        pytest.skip("network unavailable: cannot reach generativelanguage.googleapis.com (DNS lookup failed)")
    
    bb = Blackboard(inc("INC-1005"))
    await bb.mark_running(AgentName.ROUTE)
    agent = RouteAgent()
    service = GoogleAIChatCompletion(gemini_model_id=config.GEMINI_MODEL_ID, api_key=config.GEMINI_API_KEY)
    sk_agent = ChatCompletionAgent(service=service, kernel=agent.build_kernel(bb), name="RouteAgent",
                                   instructions="Call get_available_routes for destination CUST-MUM, product "
                                                "LAPTOP-001, quantity 30, excluding RT-PUN-MUM-01.",
                                   function_choice_behavior=FunctionChoiceBehavior.Auto())
    try:
        await sk_agent.get_response(messages="Investigate INC-1005")
    except Exception as exc:
        if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc) or "quota" in str(exc).lower():
            pytest.skip(f"live Gemini quota exhausted: {type(exc).__name__}")
        raise
    assert "get_available_routes" in bb.tools_invoked["RouteAgent"]
