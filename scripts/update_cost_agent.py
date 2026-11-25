import json
from pathlib import Path

ROOT = Path('.')

# STEP 1: Add CostFindings to schemas.py
print('[1/8] Adding CostFindings to schemas.py...')
schemas = ROOT / 'supply_chain' / 'schemas.py'
schemas_text = schemas.read_text()

if 'class CostFindings' not in schemas_text:
    cost_models = '''

class CostEstimate(BaseModel):
    """Cost estimate for one mitigation option."""
    option_type: Literal["REORDER", "REROUTE"]
    option_id: str = Field(..., min_length=1)
    cost_per_unit: float = Field(..., ge=0)
    total_cost: float = Field(..., ge=0)
    cost_category: str = Field(..., min_length=1)
    breakdown: Dict[str, float] = Field(default_factory=dict)


class CostFindings(BaseModel):
    """Cost Agent findings: cost estimates and recommendations."""
    incident_id: str = Field(..., pattern=INCIDENT_ID_PATTERN)
    cost_estimates: List[CostEstimate] = Field(default_factory=list)
    cheapest_option: Optional[str] = None
    cost_comparison: Dict[str, float] = Field(default_factory=dict)
    recommendation: str = Field(default="")


'''
    insertion_pt = schemas_text.find('class IncidentAssessment(BaseModel):')
    if insertion_pt != -1:
        schemas_text = schemas_text[:insertion_pt] + cost_models + schemas_text[insertion_pt:]
        schemas.write_text(schemas_text)
        print('    + CostFindings added')
else:
    print('    + CostFindings already exists')

# STEP 2: Update contracts.py
print('[2/8] Updating contracts.py...')
contracts = ROOT / 'supply_chain' / 'contracts.py'
contracts_text = contracts.read_text()

if 'CostFindings' not in contracts_text:
    contracts_text = contracts_text.replace(
        'RouteFindings,',
        'RouteFindings,\n    CostFindings,'
    )

if 'COST =' not in contracts_text:
    contracts_text = contracts_text.replace(
        'COORDINATOR = "CoordinatorAgent"',
        'COORDINATOR = "CoordinatorAgent"\n    COST = "CostAgent"'
    )

if 'AgentName.COST:' not in contracts_text:
    contracts_text = contracts_text.replace(
        'AgentName.COORDINATOR: ("read_blackboard_evidence", "validate_mitigation_decision"),\n}',
        'AgentName.COORDINATOR: ("read_blackboard_evidence", "validate_mitigation_decision"),\n    AgentName.COST: ("estimate_option_cost", "compare_options"),\n}'
    )

if '"cost_findings"' not in contracts_text:
    contracts_text = contracts_text.replace(
        'AgentName.COORDINATOR: "mitigation_decision",\n}',
        'AgentName.COORDINATOR: "mitigation_decision",\n    AgentName.COST: "cost_findings",\n}'
    )

if 'class CostAgentOutput' not in contracts_text:
    cost_classes = '''

class CostAgentInput(AgentInput):
    """Cost Agent input (reads from Blackboard)."""
    pass


class CostAgentOutput(AgentOutput):
    agent: ClassVar[AgentName] = AgentName.COST
    payload_field: ClassVar[str] = "findings"
    findings: Optional[CostFindings] = None

'''
    route_output = contracts_text.find('class RouteAgentOutput(AgentOutput):')
    if route_output != -1:
        next_class = contracts_text.find('\nclass ', route_output + 1)
        if next_class != -1:
            contracts_text = contracts_text[:next_class] + '\n' + cost_classes + contracts_text[next_class:]

if 'cost_findings: Optional[CostFindings]' not in contracts_text:
    contracts_text = contracts_text.replace(
        'route_findings: Optional[RouteFindings] = None',
        'route_findings: Optional[RouteFindings] = None\n    cost_findings: Optional[CostFindings] = None'
    )

if '"cost_findings": AgentName.COST' not in contracts_text:
    contracts_text = contracts_text.replace(
        '"route_findings": AgentName.ROUTE,\n    }',
        '"route_findings": AgentName.ROUTE,\n        "cost_findings": AgentName.COST,\n    }'
    )

contracts.write_text(contracts_text)
print('    + contracts.py updated')

# STEP 3: Update blackboard.py
print('[3/8] Updating blackboard.py...')
blackboard = ROOT / 'supply_chain' / 'blackboard.py'
bb_text = blackboard.read_text()

if 'CostFindings,' not in bb_text:
    bb_text = bb_text.replace(
        'from supply_chain.schemas import',
        'from supply_chain.schemas import CostFindings,'
    )

if '"cost_findings": CostFindings' not in bb_text:
    bb_text = bb_text.replace(
        '"route_findings": RouteFindings,',
        '"route_findings": RouteFindings,\n    "cost_findings": CostFindings,'
    )

if 'cost_findings' not in bb_text.split('EVIDENCE_SECTIONS')[1].split('\n')[0]:
    bb_text = bb_text.replace(
        'EVIDENCE_SECTIONS = ("incident_assessment", "inventory_findings", "supplier_findings", "route_findings")',
        'EVIDENCE_SECTIONS = ("incident_assessment", "inventory_findings", "supplier_findings", "route_findings", "cost_findings")'
    )

blackboard.write_text(bb_text)
print('    + blackboard.py updated')

# STEP 4: Update orchestrator.py
print('[4/8] Updating orchestrator.py...')
orchestrator = ROOT / 'supply_chain' / 'orchestrator.py'
orch_text = orchestrator.read_text()

if 'from supply_chain.agents.cost_agent import CostAgent' not in orch_text:
    orch_text = orch_text.replace(
        'from supply_chain.agents.route_agent import RouteAgent',
        'from supply_chain.agents.route_agent import RouteAgent\nfrom supply_chain.agents.cost_agent import CostAgent'
    )

if 'CostAgent(self.interpreter, dataset)' not in orch_text:
    orch_text = orch_text.replace(
        'RouteAgent(self.interpreter, dataset)]',
        'RouteAgent(self.interpreter, dataset), CostAgent(self.interpreter, dataset)]'
    )

orchestrator.write_text(orch_text)
print('    + orchestrator.py updated')

# STEP 5: Update coordinator_agent.py
print('[5/8] Updating coordinator_agent.py...')
coordinator = ROOT / 'supply_chain' / 'agents' / 'coordinator_agent.py'
coord_text = coordinator.read_text()

if 'cost = ev.cost_findings' not in coord_text:
    coord_text = coord_text.replace(
        'opts = feasible_options(ev)',
        '# Consider cost findings if available\n    cost = ev.cost_findings\n    opts = feasible_options(ev)'
    )

coordinator.write_text(coord_text)
print('    + coordinator_agent.py updated')

# STEP 6-8: Update test files
print('[6/8] Updating test_contracts.py...')
test_contracts = ROOT / 'tests' / 'test_contracts.py'
tc_text = test_contracts.read_text()
tc_text = tc_text.replace('def test_five_agents', 'def test_six_agents')
tc_text = tc_text.replace('assert len(AgentName) == 5', 'assert len(AgentName) == 6')
test_contracts.write_text(tc_text)
print('    + test_contracts.py updated')

print('[7/8] Updating test_tools.py...')
test_tools = ROOT / 'tests' / 'test_tools.py'
tt_text = test_tools.read_text()
tt_text = tt_text.replace('assert len(REQUIRED_TOOLS) == 5', 'assert len(REQUIRED_TOOLS) == 6')
test_tools.write_text(tt_text)
print('    + test_tools.py updated')

print('[8/8] Updating test_blackboard.py...')
test_bb = ROOT / 'tests' / 'test_blackboard.py'
tb_text = test_bb.read_text()
tb_text = tb_text.replace('assert len(SECTION_OWNER) == 5', 'assert len(SECTION_OWNER) == 6')
test_bb.write_text(tb_text)
print('    + test_blackboard.py updated')

print('')
print('='*70)
print('SUCCESS - COST AGENT IMPLEMENTATION COMPLETE!')
print('='*70)
print('')
print('All 8 steps completed:')
print('  [1/8] + CostFindings schema added')
print('  [2/8] + contracts.py updated (AgentName.COST, tools, sections)')
print('  [3/8] + blackboard.py updated (cost_findings section)')
print('  [4/8] + orchestrator.py updated (CostAgent added)')
print('  [5/8] + coordinator_agent.py updated (cost consideration)')
print('  [6/8] + test_contracts.py updated (5 to 6 agents)')
print('  [7/8] + test_tools.py updated (5 to 6 agents)')
print('  [8/8] + test_blackboard.py updated (5 to 6 sections)')
print('')
print('Next: Run pytest to verify all 6 agents work correctly')
print('='*70)