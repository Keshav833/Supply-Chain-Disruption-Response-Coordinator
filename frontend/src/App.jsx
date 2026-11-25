import { useEffect, useMemo, useState } from "react";

const API = import.meta.env.VITE_API_BASE_URL || "";
const AGENT_ORDER = ["MonitorAgent", "InventoryAgent", "SupplierAgent", "RouteAgent", "CoordinatorAgent"];

function titleize(value = "") {
  return value.replaceAll("_", " ").replace(/([a-z])([A-Z])/g, "$1 $2");
}

function Badge({ value }) {
  const label = String(value || "unknown").replaceAll("_", " ");
  return <span className={`badge badge-${String(value || "unknown").toLowerCase()}`}>{label}</span>;
}

function JsonPanel({ data, empty = "No data available." }) {
  if (!data) return <p className="empty">{empty}</p>;
  return <pre className="json-panel">{JSON.stringify(data, null, 2)}</pre>;
}

async function request(path, options) {
  const response = await fetch(`${API}${path}`, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || "The API request could not be completed.");
  }
  return response.json();
}

export default function App() {
  const [incidents, setIncidents] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [result, setResult] = useState(null);
  const [activeTab, setActiveTab] = useState("overview");
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    request("/api/incidents/")
      .then((items) => {
        setIncidents(items);
        setSelectedId(items[0]?.incident_id || "");
      })
      .catch((err) => setError(`Unable to load sample incidents: ${err.message}`))
      .finally(() => setLoading(false));
  }, []);

  const selectedIncident = useMemo(
    () => incidents.find((incident) => incident.incident_id === selectedId),
    [incidents, selectedId],
  );

  async function analyze() {
    if (!selectedId) return;
    setRunning(true);
    setError("");
    try {
      const run = await request(`/api/incidents/${selectedId}/analyze`, { method: "POST" });
      setResult(run);
      setActiveTab("overview");
    } catch (err) {
      setError(`Analysis could not be completed: ${err.message}`);
    } finally {
      setRunning(false);
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">SC</span><div><p>OPERATIONS CONTROL</p><h1>Disruption Response Coordinator</h1></div></div>
        <div className="mode"><span className="pulse" /> Deterministic fallback</div>
      </header>

      {error && <div className="alert" role="alert">{error}</div>}

      <section className="hero-grid">
        <div className="run-card">
          <p className="eyebrow">Incident control</p>
          <h2>Investigate a simulated disruption</h2>
          <p className="muted">The five-agent workflow gathers Blackboard evidence before recommending an unapproved mitigation.</p>
          <label htmlFor="incident">Sample incident</label>
          <select id="incident" value={selectedId} onChange={(event) => setSelectedId(event.target.value)} disabled={loading || running}>
            {incidents.map((incident) => <option key={incident.incident_id} value={incident.incident_id}>{incident.incident_id} - {titleize(incident.disruption_type)}</option>)}
          </select>
          <button type="button" onClick={analyze} disabled={!selectedId || running}>{running ? "Running agents..." : "Run analysis"}</button>
        </div>

        <div className="incident-card">
          <p className="eyebrow">Selected report</p>
          {selectedIncident ? <>
            <h2>{selectedIncident.incident_id}</h2>
            <p>{selectedIncident.description || "No description provided."}</p>
            <dl>
              <div><dt>Disruption</dt><dd>{titleize(selectedIncident.disruption_type)}</dd></div>
              <div><dt>Demand</dt><dd>{selectedIncident.required_quantity} units</dd></div>
              <div><dt>Deadline</dt><dd>{selectedIncident.deadline_days} days</dd></div>
              <div><dt>Warehouse</dt><dd>{selectedIncident.affected_warehouse_id}</dd></div>
            </dl>
          </> : <p className="empty">Loading incident catalog...</p>}
        </div>
      </section>

      {result ? <>
        <section className="decision-banner">
          <div><p className="eyebrow">Recommended action</p><h2>{result.mitigation_decision}</h2><p>{result.justification}</p></div>
          <div className="decision-meta"><Badge value={result.outcome} /><span>Run {result.run_id.slice(0, 12)}</span><span>{result.log_persistence.persisted ? "Audit log persisted" : "Audit log unavailable"}</span></div>
        </section>

        <nav className="tabs" aria-label="Analysis views">
          {["overview", "blackboard", "trace"].map((tab) => <button key={tab} className={activeTab === tab ? "active" : ""} onClick={() => setActiveTab(tab)}>{titleize(tab)}</button>)}
        </nav>

        {activeTab === "overview" && <Overview result={result} />}
        {activeTab === "blackboard" && <Blackboard result={result} />}
        {activeTab === "trace" && <Trace result={result} />}
      </> : <section className="empty-state"><h2>Ready for an investigation</h2><p>Choose a sample incident and run the five-agent analysis to see evidence, decision, and trace.</p></section>}
    </main>
  );
}

function Overview({ result }) {
  return <section className="content-grid">
    <div className="panel agents-panel"><div className="panel-heading"><div><p className="eyebrow">Workflow health</p><h2>Agent execution</h2></div><span>{Object.keys(result.agent_status).length}/5 agents</span></div>
      <div className="agent-list">{AGENT_ORDER.map((agent) => <article className="agent-row" key={agent}><div><h3>{titleize(agent.replace("Agent", ""))}</h3><p>{(result.tools_invoked[agent] || []).join(" + ") || "Awaiting execution"}</p></div><div><Badge value={result.agent_status[agent]} /><small>{result.agent_execution_modes[agent] || "not started"}</small></div></article>)}</div>
    </div>
    <div className="panel"><p className="eyebrow">Decision evidence</p><h2>Mitigation basis</h2><div className="evidence-list">{Object.entries(result.evidence_status).map(([section, evidenceState]) => <div key={section} className="evidence-row"><span>{titleize(section)}</span><Badge value={evidenceState} /></div>)}</div>
      <h3 className="subheading">Notify</h3>{result.notifications.length ? <ul>{result.notifications.map((item) => <li key={item.party}><strong>{item.party}</strong> - {item.reason}</li>)}</ul> : <p className="empty">No parties identified.</p>}
    </div>
  </section>;
}

function Blackboard({ result }) {
  return <section className="panel full-panel"><div className="panel-heading"><div><p className="eyebrow">Shared state</p><h2>Blackboard evidence</h2></div><span>Read-only run view</span></div>
    <div className="blackboard-grid">{Object.entries(result.evidence).map(([section, evidence]) => <article className="evidence-card" key={section}><div><h3>{titleize(section)}</h3><Badge value={evidence ? "available" : result.evidence_status[section]} /></div><JsonPanel data={evidence} empty="This agent did not publish evidence." /></article>)}</div>
  </section>;
}

function Trace({ result }) {
  return <section className="panel full-panel"><div className="panel-heading"><div><p className="eyebrow">Auditable sequence</p><h2>Execution trace</h2></div><span>{result.execution_trace.length} events</span></div>
    <ol className="trace-list">{result.execution_trace.map((event) => <li key={`${event.sequence}-${event.action}`}><span className="sequence">{String(event.sequence).padStart(2, "0")}</span><div><strong>{event.agent_name}</strong><p>{titleize(event.action)}</p></div><Badge value={event.status} /><time>{new Date(event.timestamp).toLocaleTimeString()}</time></li>)}</ol>
  </section>;
}
