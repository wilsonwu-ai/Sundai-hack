import { useCallback, useEffect, useState } from "react";
import { api, usd, clock, type AgentReply, type CustomerSummary, type CustomerView } from "./api";

type Panel = "send" | "dispute" | "help" | "products";

export default function Customer() {
  const [customers, setCustomers] = useState<CustomerSummary[]>([]);
  const [cid, setCid] = useState<string>("");
  const [view, setView] = useState<CustomerView | null>(null);
  const [panel, setPanel] = useState<Panel>("send");
  const [reply, setReply] = useState<AgentReply | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [disputeTxn, setDisputeTxn] = useState<string>("");

  useEffect(() => {
    api
      .customers()
      .then((cs) => {
        setCustomers(cs);
        setCid((cur) => cur || cs[0]?.customer_id || "");
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  const reload = useCallback(() => {
    if (!cid) return;
    api.customer(cid).then(setView).catch((e) => setError(String(e.message ?? e)));
  }, [cid]);

  useEffect(() => {
    setReply(null);
    setView(null);
    reload();
  }, [reload]);

  const ask = async (agent: string, task: unknown) => {
    setBusy(true);
    setError(null);
    setReply(null);
    try {
      setReply(await api.request(cid, agent, task));
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const startDispute = (txnId: string) => {
    setDisputeTxn(txnId);
    setPanel("dispute");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <div className="stack">
      <div className="row between wrap">
        <div>
          <h1>{view ? `Welcome, ${view.customer.name.split(" ")[0]}` : "Welcome"}</h1>
          <p className="muted">
            Requests below are handled by the bank's agents. Everything they do is logged for the risk team.
          </p>
        </div>
        <label className="field inline">
          <span>Signed in as</span>
          <select value={cid} onChange={(e) => setCid(e.target.value)}>
            {customers.map((c) => (
              <option key={c.customer_id} value={c.customer_id}>
                {c.name} · {c.customer_id}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <div className="banner error">{error}</div>}

      <div className="grid two">
        <section className="stack">
          <div className="cards">
            {view?.accounts.map((a) => (
              <div key={a.account_id} className="card account">
                <div className="row between">
                  <span className="label">{labelKind(a.kind)}</span>
                  <span className={`chip ${a.status === "active" ? "ok" : "warn"}`}>{a.status}</span>
                </div>
                <div className="amount">{usd(a.balance)}</div>
                <div className="muted small mono">{a.account_id}</div>
                {a.days_past_due > 0 && <div className="small warn-text">{a.days_past_due} days past due</div>}
              </div>
            ))}
            {!view && <div className="card muted">Loading…</div>}
          </div>

          <div className="card">
            <h2>Recent transactions</h2>
            <table className="table">
              <tbody>
                {view?.transactions.slice(0, 12).map((t) => (
                  <tr key={t.txn_id}>
                    <td className="muted mono small">{t.posted_at.slice(5, 10)}</td>
                    <td>
                      {t.merchant}
                      {t.status === "disputed" && <span className="chip warn tight">disputed</span>}
                    </td>
                    <td className="num">{t.amount < 0 ? "+" + usd(-t.amount) : usd(t.amount)}</td>
                    <td className="right">
                      {t.kind === "purchase" && t.status === "posted" && (
                        <button className="link" onClick={() => startDispute(t.txn_id)}>
                          Dispute
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="grid two tight">
            <div className="card">
              <h2>Payees</h2>
              {view?.beneficiaries.length ? (
                <ul className="list">
                  {view.beneficiaries.map((b) => (
                    <li key={b.beneficiary_id}>
                      <span>{b.name}</span>
                      <span className="muted mono small">•••• {b.account_last4}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">No payees yet.</p>
              )}
              <h2 className="mt">Cards</h2>
              <ul className="list">
                {view?.cards.map((c) => (
                  <li key={c.card_id}>
                    <span>
                      {c.kind} •••• {c.last4}
                    </span>
                    <span className={`chip ${c.status === "active" ? "ok" : "warn"}`}>{c.status}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="card">
              <h2>Inbox</h2>
              <ul className="list inbox">
                {view?.messages.slice(0, 8).map((m) => (
                  <li key={m.message_id}>
                    <div className="row between">
                      <strong>{m.subject}</strong>
                      <span className="muted small mono">{clock(m.sent_at)}</span>
                    </div>
                    <div className="muted small">{m.body}</div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <aside className="stack sticky">
          <div className="card">
            <div className="seg" role="tablist">
              {(
                [
                  ["send", "Send money"],
                  ["dispute", "Dispute a charge"],
                  ["help", "Get help"],
                  ["products", "Open an account"],
                ] as [Panel, string][]
              ).map(([p, label]) => (
                <button key={p} className={panel === p ? "seg-btn active" : "seg-btn"} onClick={() => setPanel(p)}>
                  {label}
                </button>
              ))}
            </div>
            {view && panel === "send" && <SendForm view={view} busy={busy} onSubmit={(t) => ask("payments", t)} />}
            {view && panel === "dispute" && (
              <DisputeForm view={view} busy={busy} initial={disputeTxn} onSubmit={(t) => ask("disputes", t)} />
            )}
            {view && panel === "help" && <HelpForm busy={busy} onSubmit={(t) => ask("support", t)} />}
            {view && panel === "products" && <ProductForm busy={busy} onSubmit={(t) => ask("sales", t)} />}
          </div>
          <Reply reply={reply} busy={busy} />
        </aside>
      </div>
    </div>
  );
}

const labelKind = (k: string) => ({ checking: "Checking", savings: "Savings", credit_card: "Credit card", loan: "Loan" })[k] ?? k;

function SendForm({ view, busy, onSubmit }: { view: CustomerView; busy: boolean; onSubmit: (t: unknown) => void }) {
  const [payee, setPayee] = useState("new");
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("250");
  const [memo, setMemo] = useState("");
  const valid = Number(amount) > 0 && (payee !== "new" || name.trim().length > 1);
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault();
        const base = { amount: Number(amount), memo: memo || undefined };
        onSubmit(payee === "new" ? { ...base, kind: "pay_new", payee_name: name.trim() } : { ...base, kind: "pay_existing", beneficiary_id: payee });
      }}
    >
      <label className="field">
        <span>To</span>
        <select value={payee} onChange={(e) => setPayee(e.target.value)}>
          <option value="new">New payee…</option>
          {view.beneficiaries.map((b) => (
            <option key={b.beneficiary_id} value={b.beneficiary_id}>
              {b.name}
            </option>
          ))}
        </select>
      </label>
      {payee === "new" && (
        <label className="field">
          <span>Payee name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Northside Property Mgmt" />
        </label>
      )}
      <div className="grid two tight">
        <label className="field">
          <span>Amount (USD)</span>
          <input type="number" min="1" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </label>
        <label className="field">
          <span>Memo</span>
          <input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="optional" />
        </label>
      </div>
      <p className="hint">
        Try <button type="button" className="link" onClick={() => { setPayee("new"); setName("Northside Property Mgmt"); setAmount("9800"); }}>$9,800 to a new payee</button>.
      </p>
      <button className="btn primary" disabled={busy || !valid}>
        {busy ? "Sending…" : "Send"}
      </button>
    </form>
  );
}

function DisputeForm({ view, busy, initial, onSubmit }: { view: CustomerView; busy: boolean; initial: string; onSubmit: (t: unknown) => void }) {
  const eligible = view.transactions.filter((t) => t.kind === "purchase" && t.status === "posted");
  const [txn, setTxn] = useState(initial || eligible[0]?.txn_id || "");
  const [reason, setReason] = useState("unrecognized");
  useEffect(() => {
    if (initial) setTxn(initial);
  }, [initial]);
  useEffect(() => {
    if (!eligible.find((t) => t.txn_id === txn)) setTxn(eligible[0]?.txn_id ?? "");
  }, [view]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ txn_id: txn, reason });
      }}
    >
      <label className="field">
        <span>Charge</span>
        <select value={txn} onChange={(e) => setTxn(e.target.value)}>
          {eligible.map((t) => (
            <option key={t.txn_id} value={t.txn_id}>
              {t.posted_at.slice(5, 10)} · {t.merchant} · {usd(t.amount)}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>What went wrong?</span>
        <select value={reason} onChange={(e) => setReason(e.target.value)}>
          <option value="unrecognized">I don't recognize it</option>
          <option value="duplicate">I was charged twice</option>
          <option value="wrong_amount">The amount is wrong</option>
          <option value="not_received">I never received it</option>
        </select>
      </label>
      <button className="btn primary" disabled={busy || !txn}>
        {busy ? "Submitting…" : "Dispute this charge"}
      </button>
    </form>
  );
}

function HelpForm({ busy, onSubmit }: { busy: boolean; onSubmit: (t: unknown) => void }) {
  const [kind, setKind] = useState("balance");
  const [field, setField] = useState("phone");
  const [value, setValue] = useState("");
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(kind === "update_contact" ? { kind, field, value: value || undefined } : { kind });
      }}
    >
      <label className="field">
        <span>How can we help?</span>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="balance">Check my balance</option>
          <option value="freeze_card">Freeze my card</option>
          <option value="update_contact">Update my contact details</option>
          <option value="reset_password">Reset my password</option>
          <option value="new_phone">I have a new phone and can't log in</option>
        </select>
      </label>
      {kind === "update_contact" && (
        <div className="grid two tight">
          <label className="field">
            <span>Field</span>
            <select value={field} onChange={(e) => setField(e.target.value)}>
              <option value="phone">Phone</option>
              <option value="email">Email</option>
              <option value="address">Address</option>
            </select>
          </label>
          <label className="field">
            <span>New value</span>
            <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="optional" />
          </label>
        </div>
      )}
      <button className="btn primary" disabled={busy}>
        {busy ? "Working…" : "Ask support"}
      </button>
    </form>
  );
}

function ProductForm({ busy, onSubmit }: { busy: boolean; onSubmit: (t: unknown) => void }) {
  const [need, setNeed] = useState("savings");
  const [consent, setConsent] = useState(false);
  return (
    <form
      className="form"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ need, consent });
      }}
    >
      <label className="field">
        <span>I'm looking for</span>
        <select value={need} onChange={(e) => setNeed(e.target.value)}>
          <option value="checking">A checking account</option>
          <option value="savings">A savings account</option>
          <option value="credit_card">A credit card</option>
          <option value="loan">A personal loan</option>
        </select>
      </label>
      <label className="check">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span>Yes, open it for me if you find a good fit</span>
      </label>
      <button className="btn primary" disabled={busy}>
        {busy ? "Looking…" : "Talk to sales"}
      </button>
    </form>
  );
}

function Reply({ reply, busy }: { reply: AgentReply | null; busy: boolean }) {
  if (busy) return <div className="card muted">The agent is working…</div>;
  if (!reply) return null;
  return (
    <div className="card">
      <div className="row between">
        <h2>What the agent did</h2>
        <span className="muted small mono">{reply.agent_id}</span>
      </div>
      {reply.paused_by_risk && (
        <div className="banner paused" role="alert">
          <strong>Paused by risk check.</strong> A step was held for review and did not run.
          {reply.gate?.reason ? <div className="small">{reply.gate.reason}</div> : null}
        </div>
      )}
      <ol className="steps">
        {reply.actions.map((a) => (
          <li key={a.step} className={a.outcome === "blocked" ? "blocked" : ""}>
            <div className="row between">
              <span className="mono">{a.action}</span>
              <span className="row gap">
                <span className={`chip pc-${a.policy_check}`}>{a.policy_check.replace("_", " ")}</span>
                {a.outcome !== "success" && <span className={`chip out-${a.outcome}`}>{a.outcome}</span>}
              </span>
            </div>
            <div className="muted small">{a.reasoning_summary}</div>
          </li>
        ))}
      </ol>
      {!reply.paused_by_risk && <p className="muted small">{reply.message}</p>}
    </div>
  );
}
