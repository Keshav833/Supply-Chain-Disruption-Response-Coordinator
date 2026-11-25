# Evaluation (PHASE_11)

The evaluation is deterministic and uses only the simulated sample data. It
does not claim that the live Gemini integration has been verified.

## What is measured

For each of the five sample incidents, the evaluator checks:

- Decision correctness against a test-only expected-outcome fixture.
- Selected supplier/route correctness when an option is required.
- Completion status for all five agents.
- Availability of each required Blackboard evidence section.
- Independent final decision validation.
- Append-only run-log persistence.

The five scenarios cover all mitigation outcomes: `REORDER`, `REROUTE`, and
`HOLD`, across supplier delay, warehouse stockout, and route blockage.
Existing resilience tests additionally cover `UNKNOWN` for missing evidence,
agent/tool failure, retry exhaustion, timeouts, and failed log persistence.

## Run evaluation

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py
.\.venv\Scripts\python.exe scripts\evaluate.py --json
```

The default run writes its temporary audit log outside the project. To retain a
specific evaluation log, choose an explicit path:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py --log-path logs\evaluation_runs.jsonl
```

## Current deterministic result

The PHASE_11 evaluation runner produced 5/5 fully passing scenarios, with
100% decision accuracy and 100% selected-option accuracy. These are regression
metrics for the simulated dataset, not a claim of real-world predictive
accuracy or live-LLM validation.
