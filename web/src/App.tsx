import { useEffect, useState } from "react";
import { API } from "./api";
import Customer from "./Customer";
import Staff from "./Staff";

type Tab = "customer" | "staff";
const fromHash = (): Tab => (location.hash === "#staff" ? "staff" : "customer");

export default function App() {
  const [tab, setTab] = useState<Tab>(fromHash());
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    const on = () => setTab(fromHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  useEffect(() => {
    let alive = true;
    const ping = () =>
      fetch(`${API}/healthz`)
        .then((r) => alive && setOnline(r.ok))
        .catch(() => alive && setOnline(false));
    ping();
    const t = setInterval(ping, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const go = (t: Tab) => {
    location.hash = t;
    setTab(t);
  };

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden />
          Sundai Bank
        </div>
        <nav className="tabs" aria-label="Sections">
          <button className={tab === "customer" ? "tab active" : "tab"} onClick={() => go("customer")}>
            Customer site
          </button>
          <button className={tab === "staff" ? "tab active" : "tab"} onClick={() => go("staff")}>
            Staff console
          </button>
        </nav>
        <div className="spacer" />
        <span className={`conn ${online === false ? "down" : online ? "up" : ""}`}>
          {online === null ? "connecting" : online ? "API online" : "API offline"}
        </span>
      </header>
      {online === false && (
        <div className="banner warn">
          Can't reach the API at <code>{API}</code>. Start it with{" "}
          <code>.venv/bin/uvicorn bank.api.server:app --port 8000</code>.
        </div>
      )}
      <main className="page">{tab === "customer" ? <Customer /> : <Staff />}</main>
    </>
  );
}
