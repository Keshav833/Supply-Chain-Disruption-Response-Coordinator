"""PHASE_8 API tests.  All workflow calls use the deterministic fallback."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from supply_chain.api.app import create_app
from supply_chain.data.sample_data import SAMPLE_INCIDENTS
from supply_chain.llm.interpreter import DeterministicInterpreter
from supply_chain.orchestrator import SupplyChainOrchestrator


def make_app(log_path):
    return create_app(lambda: SupplyChainOrchestrator(
        interpreter=DeterministicInterpreter(), log_path=log_path,
    ))


@pytest.fixture
def api_log_path(tmp_path):
    return tmp_path / "api_runs.jsonl"


def test_health_and_sample_incident_catalog(api_log_path):
    client = TestClient(make_app(api_log_path), raise_server_exceptions=False)
    health = client.get("/api/health")
    catalog = client.get("/api/incidents/")
    single = client.get("/api/incidents/INC-1001")
    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert catalog.status_code == 200 and {row["incident_id"] for row in catalog.json()} >= {"INC-1001", "INC-1002"}
    assert single.status_code == 200 and single.json()["incident_id"] == "INC-1001"


def test_sample_analysis_returns_safe_structured_run_and_result_endpoints(api_log_path):
    client = TestClient(make_app(api_log_path), raise_server_exceptions=False)
    analyzed = client.post("/api/incidents/INC-1001/analyze")
    assert analyzed.status_code == 200
    body = analyzed.json()
    assert body["mitigation_decision"] == "REORDER"
    assert body["agent_status"]["CoordinatorAgent"] == "complete"
    assert body["log_persistence"]["persisted"] is True
    assert body["execution_trace"]
    assert "log_path" not in body and "agent_errors" not in body and "errors" not in body

    run_id = body["run_id"]
    board = client.get(f"/api/incidents/INC-1001/blackboard?run_id={run_id}")
    trace = client.get(f"/api/incidents/INC-1001/trace?run_id={run_id}")
    decision = client.get(f"/api/incidents/INC-1001/decision?run_id={run_id}")
    assert board.status_code == 200 and board.json()["blackboard"]["execution_id"] == run_id
    assert trace.status_code == 200 and trace.json()["execution_trace"] == body["execution_trace"]
    assert decision.status_code == 200 and decision.json()["mitigation_decision"] == "REORDER"


def test_custom_incident_processing_and_safe_http_errors(api_log_path):
    client = TestClient(make_app(api_log_path), raise_server_exceptions=False)
    processed = client.post("/api/incidents/", json=SAMPLE_INCIDENTS[1])
    assert processed.status_code == 200
    assert processed.json()["mitigation_decision"] == "REROUTE"

    invalid = client.post("/api/incidents/", json={"incident_id": "not-valid"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "Invalid request."
    assert "input" not in str(invalid.json()).lower()

    missing = client.post("/api/incidents/INC-9999/analyze")
    no_run = client.get("/api/incidents/INC-1001/trace")
    assert missing.status_code == 404 and missing.json()["detail"] == "Sample incident not found."
    assert no_run.status_code == 404 and "No analysis run" in no_run.json()["detail"]


async def test_concurrent_api_requests_have_independent_runs_and_blackboards(api_log_path):
    app = make_app(api_log_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.post("/api/incidents/INC-1001/analyze"),
            client.post("/api/incidents/INC-1001/analyze"),
        )
        assert first.status_code == second.status_code == 200
        first_run, second_run = first.json()["run_id"], second.json()["run_id"]
        assert first_run != second_run
        boards = await asyncio.gather(
            client.get(f"/api/incidents/INC-1001/blackboard?run_id={first_run}"),
            client.get(f"/api/incidents/INC-1001/blackboard?run_id={second_run}"),
        )
    assert [item.status_code for item in boards] == [200, 200]
    assert {item.json()["blackboard"]["execution_id"] for item in boards} == {first_run, second_run}
