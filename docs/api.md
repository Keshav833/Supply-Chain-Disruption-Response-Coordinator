# FastAPI backend (PHASE_8)

The API is a thin facade over `SupplyChainOrchestrator`; it does not contain
agent logic or mitigation rules. Each processing request builds a fresh
orchestrator run, which creates its own Blackboard and unique run ID. The
bundled data is simulated and, without a real configured LLM, runs are the
deterministic fallback.

## Start the server

From the project root, install dependencies and run:

```powershell
.\.venv\Scripts\python.exe -m uvicorn supply_chain.api.app:app --reload
```

Open `http://127.0.0.1:8000/docs` for the OpenAPI interface. No API key is
needed for the deterministic fallback. Do not place keys in requests.

## Endpoints

- `GET /api/health` — service health check.
- `GET /api/incidents/` — available sample incidents.
- `GET /api/incidents/{id}` — one sample incident.
- `POST /api/incidents/{id}/analyze` — run a sample incident.
- `POST /api/incidents/` — process a valid incident JSON body.
- `GET /api/incidents/{id}/blackboard?run_id=...` — Blackboard for a completed run.
- `GET /api/incidents/{id}/trace?run_id=...` — execution trace for a completed run.
- `GET /api/incidents/{id}/decision?run_id=...` — mitigation decision for a completed run.

If `run_id` is omitted from a retrieval endpoint, the latest run for that
incident in the current server process is returned. Run history is intentionally
in-memory; the append-only JSONL log remains the durable audit record.

## Example

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/incidents/INC-1001/analyze
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/incidents/
```

The analysis response includes the run ID, per-agent statuses, tool calls,
evidence, mitigation decision, execution trace, and log-persistence status.
Validation failures use HTTP 422, missing sample incidents/runs use HTTP 404,
and unexpected internal errors use a generic HTTP 500 response without stack
traces, environment values, or log paths.

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_api.py
```
