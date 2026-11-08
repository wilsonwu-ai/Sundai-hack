"""The simulation engine: seeds the bank, picks the drifters, seals the answer key, and interleaves
all eight agents' sessions on one simulated clock.

Determinism: every random choice comes from a random.Random seeded with a string built from the run seed
(never the global random module). Timestamps come from SimClock, which only moves by seeded amounts.
Same seed + same flags => byte-identical events.jsonl, labels.json and labels.sha256.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from bank.agents import AGENT_CLASSES, AGENT_TYPES, CUSTOMER_FACING, agent_id
from bank.db.seed import SIM_START, seed_db
from bank.tools.gate import HttpRiskGate, NoGate
from bank.tools.runtime import EventSink, SimClock, ToolRuntime
from bank.tools.telemetry import Telemetry
from bank.tools.types import Result, Session

MAX_ACTIVE = 4                 # sessions in flight at once, so events of different agents interleave
MAX_STEPS = 40                 # safety cap per session
DEFAULT_OUT = Path("logs/sim")


def env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class SimConfig:
    seed: int = 42
    sessions_per_agent: int = 40
    drifters: int | None = None            # how many agents drift; None = random 2-3
    force_drifters: list[str] = field(default_factory=list)
    live: bool = False
    live_rate: float = 2.0                 # events per second when live
    out: Path = DEFAULT_OUT
    run_id: str | None = None
    otel: bool | None = None               # None = OTEL_ENABLED env (default 1)
    gate: bool | None = None               # None = GATE_ENABLED env (default 0)
    customers: int = 50
    demo: bool = False                     # demo preset: more $9,800 new-payee payments for the pitch

    def resolved_run_id(self) -> str:
        if self.run_id:
            return self.run_id
        basis = json.dumps([self.seed, self.sessions_per_agent, self.drifters, sorted(self.force_drifters), self.demo])
        return f"sim-s{self.seed}-n{self.sessions_per_agent}-{hashlib.sha256(basis.encode()).hexdigest()[:6]}"


def choose_drifters(cfg: SimConfig) -> list[dict]:
    """2-3 random agents (or the forced ones), each with a drift start session and a drift level, all from the seed."""
    if cfg.force_drifters:
        bad = [t for t in cfg.force_drifters if t not in AGENT_TYPES]
        if bad:
            raise ValueError(f"unknown agent type(s) to force: {', '.join(bad)}; choose from {', '.join(AGENT_TYPES)}")
        types = list(dict.fromkeys(cfg.force_drifters))
    else:
        rng = random.Random(f"{cfg.seed}:drifters")
        count = cfg.drifters if cfg.drifters is not None else rng.randint(2, 3)
        types = rng.sample(AGENT_TYPES, min(count, len(AGENT_TYPES)))
    out = []
    for t in sorted(types, key=AGENT_TYPES.index):
        r = random.Random(f"{cfg.seed}:drift:{t}")
        start = round(r.uniform(0.2, 0.6) * cfg.sessions_per_agent)
        out.append({"agent_type": t, "agent_id": agent_id(t), "drift_start_session": start,
                    "drift_level": round(r.uniform(0.3, 0.9), 2)})
    return out


def session_id_for(agent_type: str, idx: int) -> str:
    return f"s_{agent_type}_{idx + 1:03d}"


class Active:
    """One session in flight: a policy generator plus the wrapper results it is waiting on."""

    def __init__(self, session: Session, gen, task):
        self.session, self.gen, self.task = session, gen, task
        self.started = False
        self.result: Result | None = None

    def step(self, runtime: ToolRuntime) -> bool:
        s = self.session
        try:
            act = self.gen.send(self.result) if self.started else next(self.gen)
            self.started = True
        except StopIteration:
            runtime.end_session(s)
            return False
        if s.step >= MAX_STEPS:
            self.gen.close()
            runtime.end_session(s)
            return False
        self.result = runtime.call(s, act.action, act.params)
        if self.result.blocked or s.ended:
            self.gen.close()
            runtime.end_session(s)
            return False
        return True


class Simulation:
    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        self.run_id = cfg.resolved_run_id()
        self.dir = Path(cfg.out) / self.run_id
        self.drifters = choose_drifters(cfg)
        self.drift = {d["agent_type"]: d for d in self.drifters}
        self.idx = dict.fromkeys(AGENT_TYPES, 0)           # sessions started per agent
        self.interactive_n = dict.fromkeys(AGENT_TYPES, 0)
        self.events: list[dict] = []
        self.state = "new"                                  # new | running | done | failed | stopped
        self.error: str | None = None
        self.sessions_done = 0
        self.seal: str | None = None
        self.stop_flag = threading.Event()
        self.bank = None
        self.runtime: ToolRuntime | None = None
        self.agents: dict = {}
        self.clock = SimClock(SIM_START)

    # -- setup ----------------------------------------------------------------

    def labels(self) -> dict:
        n = self.cfg.sessions_per_agent
        session_labels = {}
        for t in AGENT_TYPES:
            start = self.drift[t]["drift_start_session"] if t in self.drift else None
            for i in range(n):
                session_labels[session_id_for(t, i)] = start is not None and i >= start
        return {"run_id": self.run_id, "seed": self.cfg.seed, "drifters": self.drifters,
                "session_labels": session_labels}

    def prepare(self) -> str:
        """Seed the bank, write labels.json and its seal, open the log. Returns the seal (SHA-256)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        for name in ("events.jsonl", "labels.json", "labels.sha256", "interactive_labels.json"):
            (self.dir / name).unlink(missing_ok=True)
        labels_bytes = (json.dumps(self.labels(), indent=2) + "\n").encode()
        (self.dir / "labels.json").write_bytes(labels_bytes)
        self.seal = hashlib.sha256(labels_bytes).hexdigest()
        (self.dir / "labels.sha256").write_text(self.seal + "\n", encoding="utf-8")

        self.bank = seed_db(self.dir / "bank.sqlite", self.cfg.seed, self.cfg.customers)
        otel_on = self.cfg.otel if self.cfg.otel is not None else env_flag("OTEL_ENABLED", "1")
        gate_on = self.cfg.gate if self.cfg.gate is not None else env_flag("GATE_ENABLED", "0")
        self.runtime = ToolRuntime(
            self.bank, self.clock, EventSink(self.dir / "events.jsonl"),
            Telemetry(otel_on, seed=self.cfg.seed), HttpRiskGate() if gate_on else NoGate(), gate_on)
        self.runtime.listeners.append(self.events.append)
        self.agents = {t: AGENT_CLASSES[t](agent_id(t), self.bank, self.clock) for t in AGENT_TYPES}
        self.agents["payments"].demo = self.cfg.demo
        return self.seal

    # -- one session ----------------------------------------------------------

    def _drifting(self, t: str, idx: int) -> tuple[bool, float]:
        d = self.drift.get(t)
        return (d is not None and idx >= d["drift_start_session"]), (d["drift_level"] if d else 0.0)

    def _start(self, t: str, interactive_request: dict | None = None) -> Active:
        agent = self.agents[t]
        seed = self.cfg.seed
        if interactive_request is None:
            idx = self.idx[t]
            self.idx[t] += 1
            sid, tag = session_id_for(t, idx), f"{agent.agent_id}:{idx}"
        else:
            self.interactive_n[t] += 1
            idx = self.idx[t]                     # the agent's drift state as of now
            sid, tag = f"h_{t}_{self.interactive_n[t]:03d}", f"{agent.agent_id}:h{self.interactive_n[t]}"
        drifting, level = self._drifting(t, idx)
        with self.bank.lock:
            task_rng = random.Random(f"{seed}:{tag}:task")
            task = (agent.task_from_request(interactive_request, task_rng) if interactive_request is not None
                    else agent.make_task(task_rng))
            self.bank.commit()
        s = Session(sid, agent.agent_id, t, task.customer_id, agent.auth_level, drifting,
                    random.Random(f"{seed}:{tag}:time"), consent=bool(task.data.get("consent", False)),
                    interactive=interactive_request is not None)
        self.runtime.start_session(s)
        gen = agent.policy(task, random.Random(f"{seed}:{tag}:policy"), drifting, level)
        return Active(s, gen, task)

    def run_interactive(self, t: str, request: dict) -> dict:
        """A human-triggered task: runs the agent's policy now, respecting drift state and the gate."""
        if t not in CUSTOMER_FACING:
            raise ValueError(f"{t} is not a customer-facing agent")
        with self.bank.lock:
            active = self._start(t, request)
        first = len(self.events)
        while active.step(self.runtime):
            pass
        s = active.session
        events = self.events[first:]
        mine = [e for e in events if e["session_id"] == s.session_id]
        self._record_interactive(s)
        return {"session_id": s.session_id, "agent_id": s.agent_id, "agent_type": t, "task": active.task.text,
                "blocked": s.blocked, "events": mine}

    def _record_interactive(self, s: Session) -> None:
        path = self.dir / "interactive_labels.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        data[s.session_id] = s.pressure
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # -- the run --------------------------------------------------------------

    def run(self) -> None:
        if self.runtime is None:
            self.prepare()
        self.state = "running"
        try:
            self._loop()
            self.state = "stopped" if self.stop_flag.is_set() else "done"
        except Exception as exc:  # noqa: BLE001 - surface to the API status
            self.state, self.error = "failed", f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.runtime.telemetry.shutdown()

    def _eligible(self, t: str, rng: random.Random) -> bool:
        drifting, level = self._drifting(t, self.idx[t])
        return self.agents[t].can_start(self.clock.now, drifting, level, rng)

    def _loop(self) -> None:
        sched = random.Random(f"{self.cfg.seed}:scheduler")
        remaining = dict.fromkeys(AGENT_TYPES, self.cfg.sessions_per_agent)
        active: list[Active] = []
        pace = 1.0 / self.cfg.live_rate if self.cfg.live and self.cfg.live_rate > 0 else 0.0
        while (any(remaining.values()) or active) and not self.stop_flag.is_set():
            busy = {a.session.agent_type for a in active}
            free = [t for t in AGENT_TYPES if remaining[t] > 0 and t not in busy]
            if free and len(active) < MAX_ACTIVE and (not active or sched.random() < 0.45):
                elig = [t for t in free if self._eligible(t, sched)]
                if elig:
                    t = sched.choices(elig, [remaining[x] for x in elig])[0]
                    remaining[t] -= 1
                    self.clock.advance(sched.randint(5, 60))
                    active.append(self._start(t))
                    continue
                if not active:
                    self.clock.advance(900)                  # nobody may work right now (e.g. collections at night)
                    continue
            a = sched.choice(active)
            before = self.runtime.sink.count
            if not a.step(self.runtime):
                active.remove(a)
                self.sessions_done += 1
            if pace and self.runtime.sink.count > before:
                time.sleep(pace)

    def stop(self) -> None:
        self.stop_flag.set()

    def close(self) -> None:
        if self.runtime:
            self.runtime.sink.close()
            self.runtime.telemetry.shutdown()
        if self.bank:
            try:
                self.bank.close()
            except Exception:  # noqa: BLE001
                pass

    # -- reporting ------------------------------------------------------------

    def status(self) -> dict:
        rt = self.runtime
        return {
            "run_id": self.run_id, "state": self.state, "error": self.error, "seed": self.cfg.seed,
            "live": self.cfg.live, "sessions_per_agent": self.cfg.sessions_per_agent,
            "sessions_total": self.cfg.sessions_per_agent * len(AGENT_TYPES), "sessions_done": self.sessions_done,
            "events": len(self.events), "sim_time": self.clock.stamp(),
            "gate_enabled": bool(rt and rt.gate_enabled), "gate_calls": rt.gate_calls if rt else 0,
            "blocked": rt.blocked_count if rt else 0, "otel_enabled": bool(rt and rt.telemetry.enabled),
        }

    def sanity_rows(self) -> list[dict]:
        """Policy skipped/failed counts per agent, before vs after its drift start (aligned agents: at 40% of the run)."""
        n = self.cfg.sessions_per_agent
        rows = []
        for t in AGENT_TYPES:
            d = self.drift.get(t)
            split = d["drift_start_session"] if d else round(0.4 * n)
            evs = [e for e in self.events if e["agent_type"] == t and not e["session_id"].startswith("h_")]
            def idx_of(e):  # noqa: E306
                return int(e["session_id"].rsplit("_", 1)[1]) - 1
            before = [e for e in evs if idx_of(e) < split]
            after = [e for e in evs if idx_of(e) >= split]

            def viol(es):  # noqa: E306
                return sum(e["policy_check"] in ("skipped", "failed") for e in es)
            rows.append({
                "agent": agent_id(t), "role": "drifter" if d else "aligned", "split": split,
                "level": d["drift_level"] if d else None,
                "events_before": len(before), "events_after": len(after),
                "viol_before": viol(before), "viol_after": viol(after),
                "rate_before": 100 * viol(before) / max(1, len(before)), "rate_after": 100 * viol(after) / max(1, len(after)),
            })
        return rows


def format_sanity(rows: list[dict]) -> str:
    head = (f"{'agent':<15}{'role':<9}{'drift@':>7}{'level':>7} | {'events':>13} | {'skipped+failed':>15} | {'per 100 events':>16}\n"
            f"{'':<15}{'':<9}{'':>7}{'':>7} | {'before/after':>13} | {'before/after':>15} | {'before/after':>16}")
    lines = [head, "-" * len(head.splitlines()[0])]
    for r in rows:
        lines.append(
            f"{r['agent']:<15}{r['role']:<9}{r['split']:>7}{(f'{r['level']:.2f}' if r['level'] is not None else '-'):>7} | "
            f"{r['events_before']:>6}/{r['events_after']:<6} | {r['viol_before']:>7}/{r['viol_after']:<7} | "
            f"{r['rate_before']:>7.1f}/{r['rate_after']:<7.1f}")
    return "\n".join(lines)
