import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, clock, type AgentInfo, type Labels, type LogEvent, type RunStatus } from "./api";

const TYPES = ["support", "payments", "disputes", "sales", "collections", "kyc", "underwriting", "ops"];
const MAX_FEED = 400;

export default function Staff() {
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<RunStatus | null>(null);
  const [seal, setSeal] = useState<string>("");
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<{ labels: Labels; sha256: string | null; sealed: string } | null>(null);
  const cursor = useRef(0);

  // follow whatever run the API considers current
  useEffect(() => {
    api
      .runs()
      .then((r) => setRunId(r.current))
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    if (!runId) return;
    cursor.current = 0;
    setEvents([]);
    setRevealed(null);
    api.seal(runId).then((s) => setSeal(s.sha256)).catch(() => setSeal(""));
  }, [runId]);

  // poll the log, the run status and the agent cards every 2 seconds
  useEffect(() => {
    if (!runId) return;
    let alive = true;
    const tick = async () => {
      try {
        const first = cursor.current === 0;
        const [logs, st, ag] = await Promise.all([
          api.logs(runId, cursor.current, first ? 200 : undefined),
          api.status(runId),
          api.agents(),
        ]);
        if (!alive) return;
        cursor.current = logs.next;
        if (logs.events.length) setEvents((prev) => [...prev, ...logs.events].slice(-MAX_FEED));
        setStatus(st);
        setAgents(ag);
        setError(null);
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [runId]);

  const onStarted = (id: string, s: string) => {
    setSeal(s);
    setRunId(id);
  };

  const reveal = async () => {
    if (!runId) return;
    try {
      setRevealed(await api.revealLabels(runId));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const drifted = useMemo(() => new Map((revealed?.labels.drifters ?? []).map((d) => [d.agent_type, d])), [revealed]);

  return (
    <div className="stack">
      <div>
        <h1>Staff console</h1>
        <p className="muted">Start a simulated day at the bank, watch every agent action as it happens, and open the sealed answer key at the end.</p>
      </div>
      {error && <div className="banner error">{error}</div>}

      <StartRun onStarted={onStarted} onError={setError} />
      <RunHeader status={status} seal={seal} onStop={() => runId && api.stopRun(runId).then(setStatus)} />

      <section>
        <h2>Agents</h2>
        <div className="agents">
          {agents.map((a) => {
            const d = drifted.get(a.agent_type);
            return (
              <div key={a.agent_id} className={`card agent ${d ? "drifted" : ""}`}>
                <div className="row between">
                  <strong>{a.agent_type}</strong>
                  <span className="chip neutral">{a.view}</span>
                </div>
                <div className="muted small agent-desc">{a.description}</div>
                <div className="row between small">
                  <span className="mono muted">{a.agent_id}</span>
                  <span>
                    {a.events} events{a.blocked > 0 && <span className="chip out-blocked tight">{a.blocked} blocked</span>}
                  </span>
                </div>
                <div className="muted small">last: {a.last_action ?? "—"}</div>
                {d && (
                  <div className="reveal-tag">
                    drifted from session {d.drift_start_session} · level {d.drift_level.toFixed(2)}
                  </div>
                )}
                {revealed && !d && <div className="aligned-tag">aligned</div>}
              </div>
            );
          })}
        </div>
      </section>

      <Feed events={events} />
      <Reveal status={status} revealed={revealed} onReveal={reveal} />
    </div>
  );
}

function StartRun({ onStarted, onError }: { onStarted: (id: string, seal: string) => void; onError: (m: string | null) => void }) {
  const [seed, setSeed] = useState(42);
  const [sessions, setSessions] = useState(40);
  const [live, setLive] = useState(true);
  const [gate, setGate] = useState(false);
  const [otel, setOtel] = useState(true);
  const [forced, setForced] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  const go = async (fn: () => Promise<{ run_id: string; seal: string }>) => {
    setBusy(true);
    onError(null);
    try {
      const r = await fn();
      onStarted(r.run_id, r.seal);
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card">
      <div className="row between wrap">
        <h2>Start a run</h2>
        <button className="btn" disabled={busy} onClick={() => go(api.startDemo)} title="Live, seed 154, payments and kyc drifting, OpenTelemetry and the gate on">
          Run demo
        </button>
      </div>
      <div className="grid four tight">
        <label className="field">
          <span>Seed</span>
          <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
        </label>
        <label className="field">
          <span>Sessions per agent</span>
          <input type="number" min={1} max={500} value={sessions} onChange={(e) => setSessions(Number(e.target.value))} />
        </label>
        <div className="field">
          <span>Options</span>
          <div className="toggles">
            <Toggle label="Live (~2 events/s)" on={live} set={setLive} />
            <Toggle label="Risk gate" on={gate} set={setGate} />
            <Toggle label="OpenTelemetry" on={otel} set={setOtel} />
          </div>
        </div>
        <div className="field">
          <span>Force drifters (optional; otherwise 2–3 at random)</span>
          <div className="chips">
            {TYPES.map((t) => (
              <button
                key={t}
                type="button"
                className={forced.includes(t) ? "pill on" : "pill"}
                onClick={() => setForced((f) => (f.includes(t) ? f.filter((x) => x !== t) : [...f, t]))}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="row">
        <button
          className="btn primary"
          disabled={busy}
          onClick={() => go(() => api.startRun({ seed, sessions_per_agent: sessions, live, force_drifters: forced, gate, otel }))}
        >
          {busy ? "Starting…" : "Start run"}
        </button>
        <span className="muted small">With the gate on but no monitor endpoint, steps fail open and the run carries on.</span>
      </div>
    </section>
  );
}

function Toggle({ label, on, set }: { label: string; on: boolean; set: (v: boolean) => void }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={on} onChange={(e) => set(e.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function RunHeader({ status, seal, onStop }: { status: RunStatus | null; seal: string; onStop: () => void }) {
  const [copied, setCopied] = useState(false);
  if (!status) return null;
  const pct = status.sessions_total ? Math.round((100 * status.sessions_done) / status.sessions_total) : 0;
  const copy = () => {
    navigator.clipboard?.writeText(seal).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <section className="card">
      <div className="row between wrap">
        <div className="row gap">
          <strong className="mono">{status.run_id}</strong>
          <span className={`chip state-${status.state}`}>{status.state}</span>
          {status.live && <span className="chip neutral">live</span>}
          {status.otel_enabled && <span className="chip neutral">otel</span>}
          {status.gate_enabled && <span className="chip neutral">gate · {status.gate_calls} calls</span>}
          {status.blocked > 0 && <span className="chip out-blocked">{status.blocked} blocked</span>}
        </div>
        {status.state === "running" && (
          <button className="btn" onClick={onStop}>
            Stop
          </button>
        )}
      </div>
      <div className="progress" aria-label={`${pct}% of sessions done`}>
        <div style={{ width: `${pct}%` }} />
      </div>
      <div className="row between small muted wrap">
        <span>
          {status.sessions_done}/{status.sessions_total} sessions · {status.events} events
        </span>
        <span className="mono">sim time {clock(status.sim_time)}</span>
      </div>
      {status.error && <div className="banner error">{status.error}</div>}
      <div className="seal">
        <div className="small muted">Answer-key seal (SHA-256 of labels.json). Post it before the risk team scores.</div>
        <div className="row gap">
          <code className="hash">{seal || "—"}</code>
          <button className="btn small" onClick={copy} disabled={!seal}>
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </div>
    </section>
  );
}

function Feed({ events }: { events: LogEvent[] }) {
  const [agent, setAgent] = useState("all");
  const [onlyFlags, setOnlyFlags] = useState(false);
  const shown = useMemo(
    () =>
      events
        .filter((e) => agent === "all" || e.agent_type === agent)
        .filter((e) => !onlyFlags || e.policy_check === "skipped" || e.policy_check === "failed" || e.outcome === "blocked")
        .slice()
        .reverse(),
    [events, agent, onlyFlags],
  );
  const fmt = useCallback((p: Record<string, unknown>) => {
    const s = Object.entries(p)
      .slice(0, 4)
      .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
      .join(" ");
    return s.length > 110 ? s.slice(0, 109) + "…" : s;
  }, []);
  return (
    <section className="card">
      <div className="row between wrap">
        <h2>Live action feed</h2>
        <div className="row gap">
          <label className="toggle">
            <input type="checkbox" checked={onlyFlags} onChange={(e) => setOnlyFlags(e.target.checked)} />
            <span>Only skipped / failed / blocked</span>
          </label>
          <select value={agent} onChange={(e) => setAgent(e.target.value)} aria-label="Filter by agent">
            <option value="all">All agents</option>
            {TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="legend small">
        <span className="chip pc-passed">passed</span>
        <span className="chip pc-skipped">skipped</span>
        <span className="chip pc-failed">failed</span>
        <span className="chip pc-not_applicable">not applicable</span>
        <span className="chip out-blocked">blocked</span>
      </div>
      <div className="feed" role="log" aria-live="off">
        {shown.length === 0 && <div className="muted pad">No events yet. Start a run.</div>}
        {shown.map((e) => (
          <div key={e.seq} className={`feed-row ${e.outcome === "blocked" ? "is-blocked" : ""} bar-${e.policy_check}`}>
            <span className="mono small muted t">{clock(e.timestamp)}</span>
            <span className="mono agent-id">{e.agent_id}</span>
            <span className="mono act">{e.action}</span>
            <span className={`chip pc-${e.policy_check}`}>{e.policy_check.replace("_", " ")}</span>
            {e.outcome !== "success" && <span className={`chip out-${e.outcome}`}>{e.outcome}</span>}
            <span className="muted small summary">{e.reasoning_summary}</span>
            <span className="mono small faint params">{fmt(e.params)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Reveal({ status, revealed, onReveal }: { status: RunStatus | null; revealed: { labels: Labels; sha256: string | null; sealed: string } | null; onReveal: () => void }) {
  const running = status?.state === "running";
  return (
    <section className="card">
      <div className="row between wrap">
        <div>
          <h2>Answer key</h2>
          <p className="muted small">Which agents drifted, and from when. Open it at the demo, after the risk team has scored.</p>
        </div>
        <button className="btn" onClick={onReveal} disabled={!status || !!revealed}>
          Reveal answer key
        </button>
      </div>
      {running && !revealed && <p className="small warn-text">The run is still in progress.</p>}
      {revealed && (
        <>
          <div className={`banner ${revealed.sha256 == null ? "warn" : revealed.sha256 === revealed.sealed ? "ok" : "error"}`}>
            {revealed.sha256 == null
              ? "This browser can't compute SHA-256 here (needs https or localhost), so the seal could not be verified."
              : revealed.sha256 === revealed.sealed
                ? "Verified: the SHA-256 of the revealed labels matches the seal posted at the start."
                : "MISMATCH: the revealed labels do not match the seal."}
            <div className="mono small">seal {revealed.sealed}</div>
            <div className="mono small">hash {revealed.sha256 ?? "—"}</div>
          </div>
          <table className="table">
            <thead>
              <tr>
                <th>Agent</th>
                <th className="num">Drift starts at session</th>
                <th className="num">Drift level</th>
                <th className="num">Sessions labelled pressure</th>
              </tr>
            </thead>
            <tbody>
              {revealed.labels.drifters.map((d) => (
                <tr key={d.agent_id}>
                  <td className="mono">{d.agent_id}</td>
                  <td className="num">{d.drift_start_session}</td>
                  <td className="num">{d.drift_level.toFixed(2)}</td>
                  <td className="num">
                    {Object.entries(revealed.labels.session_labels).filter(([sid, v]) => v && sid.includes(`_${d.agent_type}_`)).length}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
