# Demo Notebook: Supply Chain Disruption Response Coordinator

This notebook (`notebooks/demo.ipynb`) demonstrates the 6-agent Supply Chain Disruption Response Coordinator in action.

## Quick Start

### Prerequisites
- Python 3.11+ with Jupyter or JupyterLab installed
- Dependencies from `requirements.txt` installed
- Working internet connection for Jupyter (but NOT required for the demo itself—runs in deterministic mode)

### Running the Notebook

```bash
# Activate virtual environment (if using one)
source .venv/bin/activate  # Linux/Mac
# OR
.\.venv\Scripts\activate   # Windows

# Start Jupyter
jupyter notebook

# Navigate to notebooks/demo.ipynb and open it
```

Then run each cell in sequence, top to bottom.

## Demo Overview

The notebook runs **3 incident scenarios** with the entire 6-agent system:

### Scenario 1: Supplier Delay (INC-1001)
- **Context**: Supplier SUP-01 is delayed by 7 days; must fulfill 100 units to WH-NYC-01 within 10 days
- **Decision Process**:
  1. Monitor Agent validates the incident
  2. Inventory Agent checks stock available at WH-NYC-01
  3. Supplier Agent finds alternate suppliers (not SUP-01) that can fulfill the order
  4. Route Agent finds alternate routes if reordering from origin warehouse
  5. **Cost Agent ranks all viable options by total cost** (supplier unit_cost × qty + expedited fees)
  6. Coordinator selects the **cheapest viable option** that respects deadline
- **Expected Output**: REORDER decision from the cheapest supplier

### Scenario 2: Route Blockage (INC-1002)
- **Context**: Route RT-NYC-MIA-01 is blocked; must reroute 200 units to CUST-MIA within 5 days
- **Decision Process**:
  1. Monitor Agent validates the incident
  2. Inventory Agent checks stock at origin warehouse
  3. Supplier Agent is N/A (not a supplier issue)
  4. Route Agent finds alternate routes
  5. **Cost Agent ranks alternate routes by cost** (cost_per_unit × qty + expedited fees based on delivery time)
  6. Coordinator selects the **cheapest viable reroute**
- **Expected Output**: REROUTE decision via the cheapest alternate route

### Scenario 3: Invalid Incident (INC-1003)
- **Context**: Incident report is incomplete (missing `original_supplier_id` required for supplier_delay type)
- **Decision Process**:
  1. Monitor Agent detects schema validation failure
  2. Workflow stops immediately
  3. No decision is made
- **Expected Output**: UNKNOWN decision with rejection reason

## What You'll See

For each scenario, the notebook displays:

- **Incident Details**: All fields of the input report
- **Multi-Agent Execution Trace**: Ordered list of agent actions
  ```
  1. MonitorAgent        → incident_validation    → incident_assessment    [complete]
  2. InventoryAgent      → assess_stock           → inventory_findings    [complete]
  3. SupplierAgent       → find_suppliers         → supplier_findings     [complete]
  4. RouteAgent          → find_routes            → route_findings        [complete]
  5. CostAgent           → estimate_option_cost   → cost_findings         [complete]
  6. CoordinatorAgent    → propose_decision       → mitigation_decision   [complete]
  ```
- **Blackboard Sections**: All 6 evidence sections (incident_assessment, inventory_findings, supplier_findings, route_findings, **cost_findings**, mitigation_decision)
- **Final Decision**:
  - Decision type (REORDER / REROUTE / HOLD / UNKNOWN)
  - Selected option ID
  - Justification (deterministic reasoning)
  - Cost breakdown (if applicable)
  - Parties to notify
- **Execution Metadata**:
  - Run ID (for tracing/auditing)
  - Outcome (decided / undetermined / rejected / failed)
  - Execution mode (deterministic_fallback - no LLM required)
  - Duration (milliseconds)
  - Log persistence status

## Demo Log Output

After all scenarios complete, the notebook shows the **last 3 entries** from `logs/demo_runs.jsonl`:

```
Entry 1:
  Incident: INC-1001
  Agent: MonitorAgent
  Action: incident_validation
  Section: incident_assessment
  
Entry 2:
  ...
```

This log is an **append-only JSONL file** where each line is a complete structured log entry. All agent actions, evidence, and decisions are recorded for audit trails.

## Key Features Demonstrated

✓ **6-Agent Concurrency**: Monitor → Inventory/Supplier/Route/Cost (parallel) → Coordinator  
✓ **Cost Optimization**: Cost Agent determines cheapest viable option, Coordinator selects it  
✓ **Deterministic Mode**: Works offline, no LLM required  
✓ **Blackboard Architecture**: All agents write to shared evidence store  
✓ **Graceful Failure**: Invalid incidents handled safely  
✓ **Audit Trail**: All runs logged to JSONL for reproducibility  

## Execution Timeline

Each scenario typically completes in **50–200ms** depending on system load.

- Scenario 1 (Supplier Delay): ~100–150ms
- Scenario 2 (Route Blockage): ~100–150ms
- Scenario 3 (Invalid): ~10–20ms (fails fast)

**Total demo runtime**: ~1–2 seconds

## Optional: Live LLM Mode

At the end of the notebook, there is a marked cell for **Live LLM Mode**. This cell is **skipped by default** and requires:

1. A valid GEMINI_API_KEY or OPENAI_API_KEY in `.env`
2. Network access to Google AI or OpenAI APIs
3. Manual activation: set `RUN_LIVE_LLM = True` in the cell

When enabled, the Coordinator Agent will propose decisions via LLM interpretation. All proposals are still validated against the Blackboard (citation check, constraint verification).

**Note**: In sandbox environments without internet, this cell will skip gracefully.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: semantic_kernel` | Ensure dependencies are installed: `pip install -r requirements.txt` |
| `.env` permission error | The notebook handles this gracefully; make sure project runs in a writable directory |
| `PermissionError: logs/demo_runs.jsonl` | Ensure `logs/` directory is writable |
| Notebook doesn't execute | Restart Jupyter kernel and run cells sequentially from the top |
| Want to clear old demo logs | Delete `logs/demo_runs.jsonl` or restart the notebook (it auto-clears) |

## Next Steps

After running the demo:

1. **Inspect the log file**: `cat logs/demo_runs.jsonl | jq` (pretty-print JSON entries)
2. **Modify scenarios**: Change incident fields (quantities, deadlines, supplier IDs) to test different paths
3. **Add new agents**: The system is designed to extend—new agents follow the same BaseAgent pattern
4. **Review architecture**: See `README.md` and `DEVELOPMENT_STATE.md` for full system design
5. **Run the test suite**: `pytest tests/` to validate all 8 test scenarios

## References

- **Orchestrator**: `supply_chain/orchestrator.py`
- **Agents**: `supply_chain/agents/`
- **Cost Agent**: `supply_chain/agents/cost_agent.py` (6th agent added in PHASE_8.5)
- **Blackboard**: `supply_chain/blackboard.py` (shared evidence store)
- **Config**: `supply_chain/config.py` (settings and business rules)

---

**Demo Notebook Version**: 1.0  
**System**: 6-Agent Supply Chain Disruption Response Coordinator  
**Mode**: Deterministic (offline, no LLM required)  
**Last Updated**: PHASE_8.5 + PHASE_12 (demo notebook)
