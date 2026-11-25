"""Deterministic PHASE_11 evaluation runner.

This utility compares orchestrator output with the test-only expected-outcome
fixture.  It is intentionally outside ``supply_chain`` so production agents
cannot access answer keys.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from supply_chain.data.sample_data import SAMPLE_INCIDENTS
from supply_chain.llm.interpreter import DeterministicInterpreter
from supply_chain.orchestrator import SupplyChainOrchestrator

DEFAULT_EXPECTATIONS = ROOT / "tests" / "fixtures" / "expected_outcomes.json"


def load_expectations(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load the evaluation oracle from an explicit test fixture file."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row["incident_id"]: row for row in rows}


async def evaluate(expected_outcomes: Dict[str, Dict[str, Any]], log_path: Path) -> Dict[str, Any]:
    """Run every bundled incident with no LLM and return JSON-serializable metrics."""
    orchestrator = SupplyChainOrchestrator(interpreter=DeterministicInterpreter(), log_path=log_path)
    scenarios: List[Dict[str, Any]] = []
    for incident in SAMPLE_INCIDENTS:
        incident_id = incident["incident_id"]
        expected = expected_outcomes.get(incident_id)
        if expected is None:
            raise ValueError(f"No expected outcome supplied for {incident_id}")
        response = await orchestrator.run(incident)
        decision_match = response.decision.value == expected["expected_decision"]
        option_match = response.selected_option_id == expected["expected_option_id"]
        all_agents_complete = bool(response.agent_status) and all(
            status.value == "complete" for status in response.agent_status.values())
        evidence_complete = bool(response.evidence_status) and all(
            item == "AVAILABLE" for item in response.evidence_status.values())
        scenarios.append({
            "incident_id": incident_id,
            "run_id": response.run_id,
            "expected_decision": expected["expected_decision"],
            "actual_decision": response.decision.value,
            "expected_option_id": expected["expected_option_id"],
            "actual_option_id": response.selected_option_id,
            "decision_match": decision_match,
            "option_match": option_match,
            "all_agents_complete": all_agents_complete,
            "evidence_complete": evidence_complete,
            "final_validation_passed": bool(response.final_validation and response.final_validation["is_valid"]),
            "log_persisted": response.log_persisted,
        })
    total = len(scenarios)
    passed = [row for row in scenarios if all((row["decision_match"], row["option_match"],
                                                row["all_agents_complete"], row["evidence_complete"],
                                                row["final_validation_passed"], row["log_persisted"]))]
    return {
        "execution_mode": "deterministic_fallback",
        "scenario_count": total,
        "decision_accuracy": sum(row["decision_match"] for row in scenarios) / total if total else 0.0,
        "option_accuracy": sum(row["option_match"] for row in scenarios) / total if total else 0.0,
        "fully_passing_scenarios": len(passed),
        "all_scenarios_passed": len(passed) == total,
        "scenarios": scenarios,
    }


def _render(result: Dict[str, Any]) -> str:
    lines = ["PHASE_11 DETERMINISTIC EVALUATION", "=" * 36]
    for row in result["scenarios"]:
        status = "PASS" if all((row["decision_match"], row["option_match"], row["all_agents_complete"],
                                 row["evidence_complete"], row["final_validation_passed"], row["log_persisted"])) else "FAIL"
        lines.append(f"{status:4} {row['incident_id']}  {row['actual_decision']:<7} option={row['actual_option_id']}")
    lines.extend([
        "",
        f"Decision accuracy: {result['decision_accuracy']:.0%}",
        f"Option accuracy:   {result['option_accuracy']:.0%}",
        f"Full scenarios:    {result['fully_passing_scenarios']}/{result['scenario_count']}",
    ])
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic sample-incident outcomes.")
    parser.add_argument("--expected-outcomes", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument("--log-path", type=Path, default=None,
                        help="Optional evaluation-only JSONL path; defaults to a temporary location.")
    parser.add_argument("--json", action="store_true", help="Emit the full metrics document as JSON.")
    args = parser.parse_args(argv)
    log_path = args.log_path or Path(tempfile.mkdtemp(prefix="supply-chain-evaluation-")) / "runs.jsonl"
    try:
        result = asyncio.run(evaluate(load_expectations(args.expected_outcomes), log_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else _render(result))
    return 0 if result["all_scenarios_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
