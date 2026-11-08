"""CLI: python3 -m bank.agents.netsec.run --sessions 400 --seed 1 --out <dir> [--otlp URL] [--hero]"""

import argparse
import hashlib
import json
from pathlib import Path

from .generate import generate
from .hero import hero_cases
from .otel import post_otlp, to_otlp_json, traffic_to_otlp_json

DEFAULT_OUT = Path(__file__).resolve().parent / "out"


def _write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def write_toolcall_hero(hero_dir):
    """Write the seven tool-call cases (masked) as toolcall_<key>.jsonl."""
    from .toolcalls import toolcall_hero_cases
    hero_dir = Path(hero_dir)
    hero_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for key, calls in toolcall_hero_cases().items():
        p = hero_dir / f"toolcall_{key}.jsonl"
        _write_jsonl(p, calls)
        paths.append(str(p))
    return paths


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate bank API traffic and agent 10 (netsec) actions.")
    ap.add_argument("--sessions", type=int, default=400)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--otlp", default=None, help="POST traffic and agent 10 events as OTLP/JSON to this endpoint")
    ap.add_argument("--hero", action="store_true", help="also write the hero cases and the triage cases")
    ap.add_argument("--toolcalls", type=int, default=0,
                    help="also generate N tool-call sessions (third layer): toolcalls.jsonl (masked) and toolcalls_labels.json")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    g = generate(args.sessions, args.seed)
    _write_jsonl(out / "traffic.jsonl", (r for s in g["traffic"] for r in s))
    _write_jsonl(out / "netsec_events.jsonl", (e for s in g["netsec"] for e in s))
    labels_bytes = (json.dumps({"traffic": g["traffic_labels"], "netsec": g["netsec_labels"]},
                               indent=2, sort_keys=True) + "\n").encode("utf-8")
    (out / "labels.json").write_bytes(labels_bytes)  # sealed: the risk side never reads this file

    hero_paths = []
    if args.hero:
        from .hero import triage_cases
        hero_dir = out / "hero"
        hero_dir.mkdir(exist_ok=True)
        hero_labels = {}
        for key, case in hero_cases().items():
            for part in ("traffic", "netsec"):
                p = hero_dir / f"{key}_{part}.jsonl"
                _write_jsonl(p, case[part])
                hero_paths.append(str(p))
            hero_labels[key] = case["labels"]
        for key, reqs in triage_cases().items():
            p = hero_dir / f"triage_{key}.jsonl"
            _write_jsonl(p, reqs)
            hero_paths.append(str(p))
        (hero_dir / "labels.json").write_text(json.dumps(hero_labels, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.toolcalls:
        from .toolcalls import generate_toolcalls
        tc_sessions, tc_labels = generate_toolcalls(args.toolcalls, args.seed)
        _write_jsonl(out / "toolcalls.jsonl", (c for s in tc_sessions for c in s))
        tc_bytes = (json.dumps(tc_labels, indent=2, sort_keys=True) + "\n").encode("utf-8")
        (out / "toolcalls_labels.json").write_bytes(tc_bytes)  # sealed: the guard never reads this file
        print(f"toolcall_sessions={len(tc_sessions)} toolcalls={sum(len(s) for s in tc_sessions)}")
        print("toolcalls_labels.json sha256", hashlib.sha256(tc_bytes).hexdigest())
        if args.hero:
            write_toolcall_hero(out / "hero")

    if args.otlp:
        print("otlp traffic status", post_otlp(traffic_to_otlp_json(g["traffic"]), args.otlp))
        print("otlp netsec status", post_otlp(to_otlp_json(g["netsec"]), args.otlp))

    print(f"sessions={len(g['traffic'])} requests={sum(len(s) for s in g['traffic'])} "
          f"netsec_events={sum(len(s) for s in g['netsec'])} out={out}")
    for p in hero_paths:
        print("hero", p)
    print("labels.json sha256", hashlib.sha256(labels_bytes).hexdigest())


if __name__ == "__main__":
    main()
