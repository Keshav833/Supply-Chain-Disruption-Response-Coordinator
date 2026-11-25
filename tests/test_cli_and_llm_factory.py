"""CLI entry point and interpreter selection (offline; no network, no live LLM)."""
import json

import pytest

from supply_chain import config
from supply_chain import main as cli
from supply_chain.llm import factory
from supply_chain.llm.interpreter import DeterministicInterpreter, SemanticKernelInterpreter


def test_cli_runs_full_workflow(capsys, tmp_path):
    log = tmp_path / "runs.jsonl"
    code = cli.main(["--incident", "INC-1004", "--log-path", str(log), "--no-llm"])
    out = capsys.readouterr().out
    assert code == 0
    for heading in ("INCIDENT INC-1004", "AGENT EXECUTION TRACE", "EVIDENCE GATHERED", "FINAL MITIGATION DECISION",
                    "DECISION REASONING", "RUN ID", "EXECUTION MODE : deterministic_fallback"):
        assert heading in out
    assert "Decision    : HOLD" in out and "tool_call:get_available_routes" in out
    run_ids = {json.loads(line)["execution_id"] for line in log.read_text().splitlines()}
    assert len(run_ids) == 1 and next(iter(run_ids)) in out
    out.encode("ascii")  # Windows-console safe


def test_cli_all_and_json(capsys, tmp_path):
    assert cli.main(["--all", "--log-path", str(tmp_path / "r.jsonl"), "--no-llm"]) == 0
    out = capsys.readouterr().out
    assert out.count("FINAL MITIGATION DECISION") == 5
    assert cli.main(["--incident", "INC-1001", "--json", "--log-path", str(tmp_path / "r.jsonl"), "--no-llm"]) == 0
    body = capsys.readouterr().out.split("\n", 1)[1]
    assert json.loads(body)["decision"] == "REORDER"


def test_cli_invalid_inputs(capsys, tmp_path):
    assert cli.main(["--incident", "INC-0000", "--no-llm"]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"incident_id": "INC-7777", "disruption_type": "route_blockage"}))
    assert cli.main(["--incident-file", str(bad), "--log-path", str(tmp_path / "r.jsonl"), "--no-llm"]) == 2
    assert "Outcome     : rejected" in capsys.readouterr().out


def test_factory_without_keys_is_deterministic(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    interp, provider = factory.build_interpreter("gemini")
    assert isinstance(interp, DeterministicInterpreter) and provider == "none"
    assert isinstance(factory.build_interpreter("none")[0], DeterministicInterpreter)


def test_factory_builds_gemini_connector_offline(monkeypatch):
    """Constructs GoogleAIChatCompletion with a dummy key. No request is sent."""
    monkeypatch.setattr(config, "GEMINI_API_KEY", "offline-dummy-not-a-real-key")
    interp, provider = factory.build_interpreter("gemini")
    assert provider == "gemini" and isinstance(interp, SemanticKernelInterpreter) and interp.live
    assert type(interp.service).__name__ == "GoogleAIChatCompletion"
    assert interp.service.ai_model_id == config.GEMINI_MODEL_ID


def test_factory_setup_error_falls_back(monkeypatch):
    import semantic_kernel.connectors.ai.google.google_ai as g
    monkeypatch.setattr(config, "GEMINI_API_KEY", "offline-dummy-not-a-real-key")

    def boom(*a, **k):
        raise RuntimeError("bad connector")
    monkeypatch.setattr(g, "GoogleAIChatCompletion", boom)
    interp, provider = factory.build_interpreter("gemini")
    assert provider == "none" and isinstance(interp, DeterministicInterpreter)
