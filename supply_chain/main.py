"""CLI demo: run the complete five-agent workflow from the terminal.

    python -m supply_chain.main --incident INC-1001
    python -m supply_chain.main --all
    python -m supply_chain.main --incident-file my_incident.json
    python -m supply_chain.main --list

Exit codes: 0 = decision (or UNKNOWN) produced and log persisted; 1 = run failed or log not
persisted; 2 = incident rejected (invalid). Output is plain ASCII for Windows consoles.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from supply_chain import config
from supply_chain.data.sample_data import SAMPLE_INCIDENTS
from supply_chain.llm.factory import build_interpreter
from supply_chain.orchestrator import IncidentResponse, SupplyChainOrchestrator

RULE = "=" * 78


def _short(value: Any, limit: int = 110) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _trace_line(e: Dict[str, Any]) -> str:
    action = e["action"]
    detail = ""
    if action.startswith("tool_call:"):
        detail = "FAILED: " + e["error"] if e.get("error") else "ok"
    elif e.get("error"):
        detail = e["error"]
    elif action == "evidence_published":
        detail = f"-> {e['section_updated']} ({e.get('execution_mode')})"
    elif action == "run_completed":
        detail = _short(e["evidence"])
    return f"  {e['sequence']:>3}  {e['agent_name']:<17} {action:<36} {_short(detail, 60)}"


def _evidence_lines(r: IncidentResponse) -> List[str]:
    ev, out = r.evidence, []
    a = ev.get("incident_assessment")
    if a:
        out.append(f"  Monitor   : valid={a['is_valid']} severity={a['severity']} sites={a['affected_sites']}")
    inv = ev.get("inventory_findings")
    if inv:
        out.append(f"  Inventory : {inv['warehouse_id']} available={inv['available_stock']} "
                   f"safety={inv['safety_stock']} usable={inv['usable_stock']} "
                   f"replenishment_required={inv['replenishment_required']}")
    sup = ev.get("supplier_findings")
    if sup:
        for c in sup["capacity_checks"]:
            out.append(f"  Supplier  : {c['supplier_id']} feasible={c['feasible']} ({c['reason']})")
        if not sup["capacity_checks"]:
            out.append("  Supplier  : no supplier records for this product")
    rt = ev.get("route_findings")
    if rt:
        for x in rt["route_assessments"]:
            out.append(f"  Route     : {x['route_id']} {x['origin']}->{x['destination']} viable={x['viable']} "
                       f"({x['reason']})")
        if not rt["route_assessments"]:
            out.append(f"  Route     : {rt['recommendation']}")
    for section, status in r.evidence_status.items():
        if status != "AVAILABLE":
            out.append(f"  {section}: {status}")
    return out


def render(r: IncidentResponse) -> str:
    inc = r.incident or {}
    lines = [RULE, f"INCIDENT {r.incident_id or '(unknown id)'}   run_id={r.run_id}", RULE]
    if inc:
        lines += [f"  Type        : {inc.get('disruption_type')}",
                  f"  Product     : {inc.get('affected_product_id')} x {inc.get('required_quantity')}",
                  f"  Warehouse   : {inc.get('affected_warehouse_id')}  -> deliver to "
                  f"{inc.get('destination_id') or inc.get('affected_warehouse_id')}",
                  f"  Deadline    : {inc.get('deadline_days')} day(s)",
                  f"  Description : {_short(inc.get('description') or '-')}"]
    lines += ["", "AGENT EXECUTION TRACE", *(_trace_line(e) for e in r.trace), "",
              "AGENT STATUS", *(f"  {a:<17} {s.value}" + (f"  error: {r.agent_errors[a]}" if a in r.agent_errors
                                                           else "") for a, s in r.agent_status.items()), "",
              "EVIDENCE GATHERED (Blackboard)", *(_evidence_lines(r) or ["  (none)"]), "",
              "FINAL MITIGATION DECISION",
              f"  Outcome     : {r.outcome}",
              f"  Decision    : {r.decision.value}" + (f"  option={r.selected_option_id}" if r.selected_option_id else ""),
              "  Authorized  : False (recommendation only; human approval required)",
              f"  Notify      : {', '.join(n['party'] for n in r.notifications) or '-'}", "",
              "DECISION REASONING (deterministic rules + validation)", f"  {r.justification}"]
    if r.final_validation:
        lines.append(f"  Final validation: valid={r.final_validation['is_valid']} "
                     f"feasible_suppliers={r.final_validation['feasible_suppliers']} "
                     f"feasible_routes={r.final_validation['feasible_routes']}")
    if r.interpretation:
        lines += ["", "EXPLANATION (interpreter text, not used for the decision)", f"  {_short(r.interpretation, 300)}"]
    lines += ["", f"EXECUTION MODE : {r.execution_label}",
              f"RUN ID         : {r.run_id}",
              f"RUN LOG        : {'appended ' + str(r.log_entries_written) + ' entries to ' + str(r.log_path) if r.log_persisted else 'NOT PERSISTED - ' + str(r.log_error)}",
              f"DURATION       : {r.duration_ms} ms"]
    if r.errors:
        lines += ["ERRORS         :", *(f"  - {e}" for e in r.errors)]
    return "\n".join(lines)


def _exit_code(r: IncidentResponse) -> int:
    if r.outcome == "rejected":
        return 2
    return 0 if r.outcome in ("decided", "undetermined") and r.log_persisted else 1


def main(argv: Optional[List[str]] = None) -> int:
    config.configure_logging()
    parser = argparse.ArgumentParser(prog="python -m supply_chain.main",
                                     description="Supply Chain Disruption Response Coordinator (CLI demo)")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--incident", help="sample incident id, e.g. INC-1001")
    src.add_argument("--incident-file", type=Path, help="JSON file with one incident report")
    src.add_argument("--all", action="store_true", help="run all sample incidents")
    src.add_argument("--list", action="store_true", help="list sample incidents")
    parser.add_argument("--log-path", type=Path, default=None, help=f"JSONL run log (default {config.INCIDENT_LOG_PATH})")
    parser.add_argument("--no-llm", action="store_true", help="force deterministic fallback even if a key is set")
    parser.add_argument("--json", action="store_true", help="print the full IncidentResponse as JSON")
    args = parser.parse_args(argv)

    if args.list:
        for d in SAMPLE_INCIDENTS:
            print(f"{d['incident_id']}  {d['disruption_type']:<19} {d['description']}")
        return 0
    if args.incident:
        matches = [d for d in SAMPLE_INCIDENTS if d["incident_id"] == args.incident]
        if not matches:
            print(f"Unknown incident {args.incident}. Use --list.", file=sys.stderr)
            return 2
        incidents = matches
    elif args.incident_file:
        incidents = [json.loads(args.incident_file.read_text(encoding="utf-8"))]
    else:
        incidents = list(SAMPLE_INCIDENTS)

    interpreter, provider = build_interpreter("none" if args.no_llm else None)
    print(f"LLM provider: {provider}" + (" (deterministic fallback; no live LLM)" if provider == "none" else
                                         " (configured; each output is labelled by what actually ran)"))
    orchestrator = SupplyChainOrchestrator(interpreter=interpreter, log_path=args.log_path)
    code = 0
    for d in incidents:
        response = asyncio.run(orchestrator.run(d))
        print(response.model_dump_json(indent=2) if args.json else render(response))
        code = max(code, _exit_code(response))
    return code


if __name__ == "__main__":
    sys.exit(main())
