# Standing Rules for Supply Chain Disruption Response Coordinator (CLAUDE.md)

**Project:** CSE476 Task 2, Q6 — Supply Chain Disruption Response Coordinator

**Last updated:** 2026-11-18

---

## 1. Project Context

- **Architecture:** Blackboard (shared state)
- **Framework:** Semantic Kernel only (no alternatives, no fake SK)
- **Agents:** 5 modules (Monitor, Inventory, Supplier, Route, Coordinator)
- **Tools:** 2 plain-Python functions per agent (deterministic, testable, no mocking in production)
- **Data:** Simulated JSON datasets; queries live in tools, not orchestrator

---

## 2. Never Fake LLM Behaviour

- Every run must be labelled with its true execution mode:
  - `ExecutionMode.DETERMINISTIC_FALLBACK` → no live LLM call succeeded
  - `ExecutionMode.SEMANTIC_KERNEL_LLM` → a real Semantic Kernel LLM call returned text
- Never present scripted or test-double output as live. Test mocks (ScriptedTextService, etc.) must set `live=False` in `SemanticKernelInterpreter`.
- If a tool or interpreter fails, labelling is still correct (failure is not an LLM execution).

---

## 3. Coordinator Decision Authority

- The Coordinator **reads Blackboard evidence only.**
- The Coordinator has **no access to raw datasets** (inventory.json, suppliers.json, routes.json, catalog.json).
- Decisions are deterministic from published Blackboard sections:
  - incident_assessment (Monitor)
  - inventory_findings (Inventory)
  - supplier_findings (Supplier)
  - route_findings (Route)
- Decision logic: `propose_decision()` in coordinator_agent.py.
- Validation: `validate_mitigation_decision()` in coordinator_tools.py (re-checks feasibility from evidence).
- If evidence is missing/failed/contradictory → decision is UNKNOWN (never guessed).

---

## 4. Run Log is Append-Only JSONL

- **File:** `logs/incident_runs.jsonl`
- **Format:** One `AgentLogEntry` JSON per line
- **Write semantics:** `append_jsonl()` uses `open(..., "a")` with thread lock; never truncates or rewrites
- **Atomicity:** One batch write per run (all entries for one incident written together)
- **On failure:** Persistence error is surfaced in `IncidentResponse.log_error` and `log_persisted: bool`
- **Never:** Delete, truncate, or rewrite existing log entries

---

## 5. Do Not Break Existing Tests

- **Before every task:** Run the full test suite: `.\.venv\Scripts\python.exe -m pytest -q --tb=short 2>&1`
- **After every task:** Run the full test suite again and report both pass/fail counts
- **Acceptable changes:** New passing tests, bug fixes that increase pass count
- **Unacceptable changes:** Increased fail count, skipped tests without reason, modified test assertions
- **Report format:** `Before: X passed; After: X passed` or `After: X passed, 1 failed` with reason

---

## 6. Keep Changes Minimal and Local

- **Scope:** Only the files needed for the task
- **No unrelated refactoring:** If a file needs a change, make only that change
- **No ripple effects:** If you touch schemas, check contracts and all agents; if you touch blackboard, check orchestrator
- **Never:** Rewrite unrelated functions, rename things unnecessarily, reorganize imports, delete commented code
- **Frontend:** Off-limits. Never edit `frontend/`, `frontend/**/*.js`, `frontend/**/*.json`, etc.

---

## 7. Never Commit Secrets

- **API keys:** Read only from environment (`.env`, not `.env.example`)
- **In code:** Use `config.py` to load from env, never hard-code values
- **In tests:** Use fixtures or mocks; never embed real keys
- **In logs:** Never echo API keys, credentials, or secrets to stdout or files
- **.gitignore:** Confirm `.env` is listed (not `.env.example`), private keys are ignored

---

## 8. End-of-Task Checklist

After every task:

1. **Update DEVELOPMENT_STATE.md:**
   - What changed (which files, which phase)
   - Test results (before and after)
   - Any new known issues or blockers
   - Next action

2. **Give a short summary:**
   - 1–2 sentences describing the change
   - Any tradeoffs or decisions made
   - Which hard requirements are now met (if applicable)

3. **Provide exact verification commands:**
   - Full pytest command to run
   - Any manual checks (curl, CLI command, notebook)
   - Expected output snippet

---

## Reference Commands

```powershell
# Run full test suite
.\.venv\Scripts\python.exe -m pytest -q --tb=short

# Run one test file
.\.venv\Scripts\python.exe -m pytest -q tests/test_orchestrator.py

# Run CLI demo
.\.venv\Scripts\python.exe -m supply_chain.main --incident INC-1001 --no-llm

# Check config
.\.venv\Scripts\python.exe -c "from supply_chain import config; print(config.LLM_PROVIDER, config.INCIDENT_LOG_PATH)"

# List agents and tools
.\.venv\Scripts\python.exe -c "from supply_chain.contracts import REQUIRED_TOOLS, BLACKBOARD_SECTION; print(REQUIRED_TOOLS); print(BLACKBOARD_SECTION)"
```

---

## Known Issues (Current)

- **Gemini model (gemini-2.5-flash) retired:** No live LLM verified. Set `GEMINI_API_KEY` to real key + valid model ID to verify.
- **Missing PHASE_12 deliverables:** README, architecture diagram, demo notebook. Needed for submission.
- **npm audit warnings:** 4 high-severity (frontend, out of scope).

---

## Phase Tracking

- **Current:** PHASE_11 (Evaluation complete)
- **TODO:** PHASE_12 (README, diagram, notebook)

