# Reliability and logging (PHASE_10)

The project is an academic MVP. It preserves evidence integrity and auditability;
it does not claim high availability or distributed durability.

## Runtime safeguards

- Every analysis receives a unique run ID and a new Blackboard instance.
- Each agent has a bounded `AGENT_TIMEOUT_SECONDS` deadline (default: 60).
- Specialist failures are isolated: the remaining agents publish their evidence,
  while failed and missing sections remain `FAILED` or `UNKNOWN`, never negative.
- The Coordinator runs only after specialist tasks complete and can decide only
  from Blackboard evidence.
- The final deterministic validation gate rejects unsupported published decisions.

## Tool retry policy

At the shared Semantic Kernel plugin boundary, a tool is retried only when it
raises `ConnectionError`, `TimeoutError`, or `OSError`. Invalid input, invalid
tool output, and business-data errors fail immediately. Defaults are controlled
through environment variables:

```text
TOOL_RETRY_MAX_ATTEMPTS=3
TOOL_RETRY_DELAY_SECONDS=0.05
AGENT_TIMEOUT_SECONDS=60
```

The delay uses bounded exponential backoff (0.05s, then 0.10s by default).
Every failed attempt and every retry is recorded in the execution trace; only a
successful call appears in an agent's `tools_invoked` list.

## Audit log behavior

Blackboard events are sequenced per run and written as append-only JSONL.
Writes are serialized inside the process, so concurrent API requests cannot
interleave a single run's log batch. A persistence failure is surfaced in the
structured response; the system does not claim the log was stored.

Use the test suite to exercise retry success, immediate non-retryable failure,
retry exhaustion, agent timeout, and persistence failure:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_resilience.py tests/test_orchestrator.py
```
