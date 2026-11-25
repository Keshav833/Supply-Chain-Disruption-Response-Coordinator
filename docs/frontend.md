# Frontend dashboard (PHASE_9)

The React/Vite dashboard is a presentation layer only. It calls the PHASE_8
FastAPI endpoints and never reproduces mitigation decisions or agent logic in
the browser.

## Run locally

Start the API in one terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn supply_chain.api.app:app --reload
```

Install dashboard dependencies once, then start Vite in another terminal:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Open the address shown by Vite (normally `http://127.0.0.1:5173`). The Vite
development server proxies `/api` calls to FastAPI on port 8000. Set
`VITE_API_BASE_URL` only when the API is hosted elsewhere.

## Included views

- Sample-incident selection and analysis trigger.
- Recommendation, justification, run ID, and audit-log status.
- Per-agent status and invoked tools.
- Blackboard evidence explorer.
- Ordered execution trace.

The dashboard labels the deterministic fallback honestly. It is not an
authorization interface: recommendations still require human approval.
