"""Run a Sundai Bank simulation.

    python -m bank.sim.run_sim --seed 42
    python -m bank.sim.run_sim --seed 42 --force-drifters payments,kyc --live
    python -m bank.sim.run_sim --seed 42 --drifters 0          # control run: every agent aligned
    python -m bank.sim.run_sim --seed 42 --drift-plan ops:12:0.48,sales:5:0.33   # explicit drifters

Writes logs/sim/<run_id>/{events.jsonl, labels.json, labels.sha256, bank.sqlite}. The seal (SHA-256 of
labels.json) is printed before anything runs, so it can be posted before the risk team scores.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from bank.agents import AGENT_TYPES
from bank.sim.engine import DEFAULT_OUT, SimConfig, Simulation, format_sanity


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--sessions-per-agent", type=int, default=40)
    p.add_argument("--drifters", type=int, default=None, help="how many agents drift (default: random 2-3; 0 = every agent aligned)")
    p.add_argument("--force-drifters", default="", help=f"comma list from: {','.join(AGENT_TYPES)} (for the demo)")
    p.add_argument("--drift-plan", default="",
                   help="explicit drifters as type:first_rogue_session:level,... (first_rogue_session is 1-based, "
                        "as in session ids), e.g. ops:12:0.48,sales:5:0.33")
    p.add_argument("--live", action="store_true", help="emit events gradually, about 2 per second")
    p.add_argument("--live-rate", type=float, default=2.0, help="events per second with --live")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="base folder; the run goes in <out>/<run_id>/")
    p.add_argument("--run-id", default=None)
    p.add_argument("--demo", action="store_true", help="demo preset: more $9,800 new-payee payments (see demo.sh)")
    p.add_argument("--otel", dest="otel", action="store_true", default=None, help="force OpenTelemetry on")
    p.add_argument("--no-otel", dest="otel", action="store_false", help="force OpenTelemetry off")
    p.add_argument("--gate", dest="gate", action="store_true", default=None, help="force the risk gate on")
    p.add_argument("--no-gate", dest="gate", action="store_false", help="force the risk gate off")
    return p


def parse_drift_plan(text: str) -> dict[str, tuple[int, float]]:
    """"ops:12:0.48,sales:5:0.33" -> {"ops": (11, 0.48), "sales": (4, 0.33)} (1-based session -> 0-based index)."""
    plan = {}
    for item in (x.strip() for x in text.split(",") if x.strip()):
        try:
            t, first, level = item.split(":")
            first_i, level_f = int(first), float(level)
        except ValueError:
            raise ValueError(f"bad drift plan entry {item!r}; expected type:first_rogue_session:level") from None
        if first_i < 1:
            raise ValueError(f"{t}: first rogue session must be 1 or more")
        if t in plan:
            raise ValueError(f"{t} appears twice in the drift plan")
        plan[t] = (first_i - 1, level_f)
    return plan


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        drift_plan = parse_drift_plan(args.drift_plan) if args.drift_plan.strip() else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cfg = SimConfig(
        seed=args.seed, sessions_per_agent=args.sessions_per_agent, drifters=args.drifters,
        force_drifters=[t.strip() for t in args.force_drifters.split(",") if t.strip()], live=args.live,
        live_rate=args.live_rate, out=Path(args.out), run_id=args.run_id, otel=args.otel, gate=args.gate,
        demo=args.demo, drift_plan=drift_plan)
    try:
        sim = Simulation(cfg)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    seal = sim.prepare()
    print(f"run_id   {sim.run_id}")
    print(f"seal     {seal}   (SHA-256 of {sim.dir / 'labels.json'})")
    print(f"gate={'on' if sim.runtime.gate_enabled else 'off'}  otel={'on' if sim.runtime.telemetry.enabled else 'off'}"
          f"  live={'on' if cfg.live else 'off'}")
    try:
        sim.run()
    except KeyboardInterrupt:
        sim.stop()
        print("\ninterrupted", file=sys.stderr)
    finally:
        sim.close()
    st = sim.status()
    print(f"\n{st['events']} events from {st['sessions_done']} sessions -> {sim.dir / 'events.jsonl'}"
          f"  (gate calls {st['gate_calls']}, blocked {st['blocked']})\n")
    print(format_sanity(sim.sanity_rows()))
    return 0 if sim.state in ("done", "stopped") else 1


if __name__ == "__main__":
    sys.exit(main())
