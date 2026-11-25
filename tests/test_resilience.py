"""PHASE_10: retries, timeout boundaries, and append-only audit resilience."""
from __future__ import annotations

import pytest

from supply_chain import config
from supply_chain.agents import route_agent
from supply_chain.data.sample_data import SAMPLE_INCIDENTS
from supply_chain.orchestrator import run_incident
from supply_chain.schemas import AgentStatus, MitigationDecision


def incident(incident_id: str):
    return next(item for item in SAMPLE_INCIDENTS if item["incident_id"] == incident_id)


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / "logs" / "incident_runs.jsonl"


async def test_recoverable_tool_failure_retries_then_preserves_success(monkeypatch, log_path):
    original = route_agent.get_available_routes
    calls = 0

    def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary route service outage")
        return original(*args, **kwargs)

    monkeypatch.setattr(route_agent, "get_available_routes", flaky)
    monkeypatch.setattr(config, "TOOL_RETRY_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(config, "TOOL_RETRY_DELAY_SECONDS", 0)
    response = await run_incident(incident("INC-1002"), log_path=log_path)

    assert calls == 2
    assert response.decision is MitigationDecision.REROUTE
    assert response.agent_status["RouteAgent"] is AgentStatus.COMPLETE
    retries = [event for event in response.trace if event["action"] == "tool_retry:get_available_routes"]
    assert len(retries) == 1
    assert retries[0]["evidence"] == {"attempt": 1, "max_attempts": 2}
    assert response.tools_invoked["RouteAgent"].count("get_available_routes") == 1


async def test_nonrecoverable_tool_error_is_not_retried(monkeypatch, log_path):
    calls = 0

    def invalid(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise ValueError("invalid source data")

    monkeypatch.setattr(route_agent, "get_available_routes", invalid)
    monkeypatch.setattr(config, "TOOL_RETRY_MAX_ATTEMPTS", 3)
    response = await run_incident(incident("INC-1002"), log_path=log_path)

    assert calls == 1
    assert response.agent_status["RouteAgent"] is AgentStatus.FAILED
    assert response.decision is MitigationDecision.UNKNOWN
    assert not any(event["action"] == "tool_retry:get_available_routes" for event in response.trace)


async def test_retry_is_bounded_when_transient_error_persists(monkeypatch, log_path):
    calls = 0

    def unavailable(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise ConnectionError("route service unavailable")

    monkeypatch.setattr(route_agent, "get_available_routes", unavailable)
    monkeypatch.setattr(config, "TOOL_RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(config, "TOOL_RETRY_DELAY_SECONDS", 0)
    response = await run_incident(incident("INC-1002"), log_path=log_path)

    assert calls == 3
    assert response.agent_status["RouteAgent"] is AgentStatus.FAILED
    assert len([event for event in response.trace if event["action"] == "tool_retry:get_available_routes"]) == 2
