"""PHASE_11: deterministic end-to-end evaluation metrics."""
from __future__ import annotations

from pathlib import Path

from scripts.evaluate import evaluate, load_expectations


async def test_evaluation_matches_all_sample_oracles_and_integrity_controls(tmp_path):
    expected_path = Path(__file__).parent / "fixtures" / "expected_outcomes.json"
    result = await evaluate(load_expectations(expected_path), tmp_path / "evaluation.jsonl")

    assert result["execution_mode"] == "deterministic_fallback"
    assert result["scenario_count"] == 5
    assert result["decision_accuracy"] == result["option_accuracy"] == 1.0
    assert result["fully_passing_scenarios"] == 5 and result["all_scenarios_passed"] is True
    assert len({row["run_id"] for row in result["scenarios"]}) == 5
    assert all(row["all_agents_complete"] and row["evidence_complete"]
               and row["final_validation_passed"] and row["log_persisted"] for row in result["scenarios"])
