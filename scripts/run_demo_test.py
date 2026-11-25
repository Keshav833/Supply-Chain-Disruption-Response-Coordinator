import asyncio, json, sys, os
from pathlib import Path

os.environ['LOG_LEVEL'] = 'INFO'
os.environ['INCIDENT_LOG_PATH'] = 'logs/incident_runs.jsonl'
os.environ['LLM_PROVIDER'] = 'none'

project_root = Path.cwd()
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

print("[INFO] Initializing demo notebook runner...")
try:
    from supply_chain.orchestrator import SupplyChainOrchestrator
    from supply_chain.contracts import AgentName
    print("[OK] Orchestrator imported")
    print(f"[OK] System has {len(list(AgentName))} agents")
except Exception as e:
    print(f"[FAIL] Import failed: {str(e)[:200]}")
    sys.exit(1)

demo_log = Path("logs/demo_runs.jsonl")
demo_log.parent.mkdir(exist_ok=True)
if demo_log.exists():
    demo_log.unlink()

orch = SupplyChainOrchestrator(log_path=demo_log)

async def main():
    print("\n" + "="*80)
    print("SCENARIO 1: Supplier Delay")
    print("="*80)
    
    inc1 = {
        "incident_id": "INC-1001",
        "disruption_type": "supplier_delay",
        "affected_product_id": "XYZ-001",
        "affected_warehouse_id": "WH-NYC-01",
        "required_quantity": 100,
        "original_supplier_id": "SUP-01",
        "reported_delay_days": 7,
        "deadline_days": 10,
    }
    
    print(f"Incident: {inc1['incident_id']} ({inc1['disruption_type']})")
    r1 = await orch.run(inc1)
    print(f"Decision: {r1.decision.value}")
    print(f"Option: {r1.selected_option_id}")
    print(f"Outcome: {r1.outcome}")
    print(f"Duration: {r1.duration_ms:.1f}ms")
    
    print("\n" + "="*80)
    print("SCENARIO 2: Route Blockage")
    print("="*80)
    
    inc2 = {
        "incident_id": "INC-1002",
        "disruption_type": "route_blockage",
        "affected_product_id": "ABC-050",
        "affected_warehouse_id": "WH-NYC-01",
        "required_quantity": 200,
        "destination_id": "CUST-MIA",
        "original_route_id": "RT-NYC-MIA-01",
        "deadline_days": 5,
    }
    
    print(f"Incident: {inc2['incident_id']} ({inc2['disruption_type']})")
    r2 = await orch.run(inc2)
    print(f"Decision: {r2.decision.value}")
    print(f"Option: {r2.selected_option_id}")
    print(f"Outcome: {r2.outcome}")
    print(f"Duration: {r2.duration_ms:.1f}ms")
    
    print("\n" + "="*80)
    print("SCENARIO 3: Invalid Incident")
    print("="*80)
    
    inc3 = {
        "incident_id": "INC-1003",
        "disruption_type": "supplier_delay",
        "affected_product_id": "DEF-100",
        "affected_warehouse_id": "WH-LAX-02",
        "required_quantity": 50,
    }
    
    print(f"Incident: {inc3['incident_id']} (INCOMPLETE - missing required field)")
    r3 = await orch.run(inc3)
    print(f"Decision: {r3.decision.value}")
    print(f"Outcome: {r3.outcome}")
    
    print("\n" + "="*80)
    print("DEMO LOG OUTPUT")
    print("="*80)
    
    if demo_log.exists():
        with open(demo_log) as f:
            lines = f.readlines()
        print(f"\nTotal entries: {len(lines)}")
        print("\nLast 3 entries:")
        for line in lines[-3:]:
            e = json.loads(line)
            print(f"  INC: {e.get('incident_id')} | AGENT: {e.get('agent_name')} | ACTION: {e.get('action')}")
    
    print("\n" + "="*80)
    print("DEMO COMPLETED SUCCESSFULLY")
    print("="*80)
    print(f"Results: {demo_log}")

try:
    asyncio.run(main())
except Exception as e:
    print(f"[ERROR] {str(e)[:300]}")
    import traceback
    traceback.print_exc()
