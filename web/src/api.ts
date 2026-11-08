export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export type PolicyCheck = "passed" | "skipped" | "failed" | "not_applicable";
export type Outcome = "success" | "error" | "blocked";

export interface LogEvent {
  seq: number;
  agent_id: string;
  agent_type: string;
  session_id: string;
  step: number;
  timestamp: string;
  action: string;
  params: Record<string, unknown>;
  auth_level: string;
  policy_check: PolicyCheck;
  outcome: Outcome;
  reasoning_summary: string;
}

export interface RunStatus {
  run_id: string;
  state: "new" | "idle" | "running" | "done" | "failed" | "stopped";
  error: string | null;
  seed: number;
  live: boolean;
  sessions_total: number;
  sessions_done: number;
  events: number;
  sim_time: string;
  gate_enabled: boolean;
  gate_calls: number;
  blocked: number;
  otel_enabled: boolean;
}

export interface AgentInfo {
  agent_id: string;
  agent_type: string;
  view: "customer" | "internal";
  description: string;
  actions: string[];
  events: number;
  blocked: number;
  last_action: string | null;
}

export interface StartBody {
  seed: number;
  sessions_per_agent: number;
  live: boolean;
  force_drifters: string[];
  gate: boolean;
  otel: boolean;
}

export interface Labels {
  run_id: string;
  seed: number;
  drifters: { agent_type: string; agent_id: string; drift_start_session: number; drift_level: number }[];
  session_labels: Record<string, boolean>;
}

export interface CustomerSummary {
  customer_id: string;
  name: string;
  segment: string;
}

export interface Account {
  account_id: string;
  kind: string;
  balance: number;
  status: string;
  days_past_due: number;
}
export interface Txn {
  txn_id: string;
  kind: string;
  amount: number;
  merchant: string;
  posted_at: string;
  status: string;
}
export interface Beneficiary {
  beneficiary_id: string;
  name: string;
  account_last4: string;
  payments_count: number;
}
export interface Card {
  card_id: string;
  last4: string;
  kind: string;
  status: string;
}
export interface Message {
  message_id: string;
  subject: string;
  body: string;
  sent_at: string;
  sender: string;
}
export interface CustomerView {
  customer: { customer_id: string; name: string; email: string; phone: string; address: string; segment: string };
  accounts: Account[];
  transactions: Txn[];
  beneficiaries: Beneficiary[];
  cards: Card[];
  messages: Message[];
  disputes: { dispute_id: string; txn_id: string; status: string; credited_amount: number }[];
}

export interface AgentAction {
  step: number;
  action: string;
  params: Record<string, unknown>;
  policy_check: PolicyCheck;
  outcome: Outcome;
  reasoning_summary: string;
}
export interface AgentReply {
  session_id: string;
  agent_id: string;
  task: string;
  paused_by_risk: boolean;
  gate: { decision: string | null; reason: string | null } | null;
  message: string;
  actions: AgentAction[];
}

export const api = {
  startRun: (b: StartBody) => j<{ run_id: string; seal: string }>("/sim/start", { method: "POST", body: JSON.stringify(b) }),
  startDemo: () => j<{ run_id: string; seal: string }>("/sim/demo", { method: "POST" }),
  stopRun: (id: string) => j<RunStatus>(`/sim/${id}/stop`, { method: "POST" }),
  status: (id: string) => j<RunStatus>(`/sim/${id}/status`),
  runs: () => j<{ current: string; runs: RunStatus[] }>("/runs"),
  seal: (id: string) => j<{ sha256: string }>(`/sim/${id}/seal`),
  logs: (runId: string, since: number, tail?: number) =>
    j<{ run_id: string; state: string; next: number; total: number; events: LogEvent[] }>(
      `/logs?run_id=${encodeURIComponent(runId)}&since=${since}${tail ? `&tail=${tail}` : ""}`,
    ),
  agents: () => j<AgentInfo[]>("/agents"),
  customers: () => j<CustomerSummary[]>("/customers"),
  customer: (id: string) => j<CustomerView>(`/customers/${id}`),
  request: (id: string, agent_type: string, task: unknown) =>
    j<AgentReply>(`/customer/${id}/request`, { method: "POST", body: JSON.stringify({ agent_type, task }) }),
  /** The sealed bytes, verbatim, so the browser can hash exactly what the server sealed. */
  async revealLabels(id: string): Promise<{ labels: Labels; sha256: string | null; sealed: string }> {
    const res = await fetch(`${API}/sim/${id}/labels`);
    if (!res.ok) throw new Error(res.statusText);
    const bytes = await res.arrayBuffer();
    const sealed = res.headers.get("X-Seal-Sha256") ?? "";
    let sha256: string | null = null;
    if (globalThis.crypto?.subtle) {
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      sha256 = Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
    }
    return { labels: JSON.parse(new TextDecoder().decode(bytes)) as Labels, sha256, sealed };
  },
};

export const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
export const clock = (iso: string) => iso.slice(5, 10) + " " + iso.slice(11, 19);
