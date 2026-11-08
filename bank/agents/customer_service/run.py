"""CLI: python3 -m bank.agents.customer_service.run --sessions 500 --seed 1 --out <dir> [--otlp URL] [--hero]"""

import argparse
import hashlib
import json
from pathlib import Path

from .generate import generate_full
from .hero import hero_cases
from .otel import post_otlp, to_otlp_json

DEFAULT_OUT = Path(__file__).resolve().parent / "out"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate service agent sessions.")
    ap.add_argument("--sessions", type=int, default=500)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--otlp", default=None, help="POST the sessions as OTLP/JSON to this endpoint")
    ap.add_argument("--hero", action="store_true", help="also write the four Amazon hero sessions")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sessions, labels, requests = generate_full(args.sessions, args.seed)

    with open(out / "events.jsonl", "w", encoding="utf-8") as fh:
        for events in sessions:
            for e in events:
                fh.write(json.dumps(e) + "\n")
    labels_bytes = (json.dumps(labels, indent=2, sort_keys=True) + "\n").encode("utf-8")
    (out / "labels.json").write_bytes(labels_bytes)
    with open(out / "requests.jsonl", "w", encoding="utf-8") as fh:  # customer messages, no labels
        for sid, text in requests.items():
            fh.write(json.dumps({"session_id": sid, "text": text}) + "\n")

    if args.hero:
        hero_dir = out / "hero"
        hero_dir.mkdir(exist_ok=True)
        for key, events in hero_cases().items():
            with open(hero_dir / f"{key}.jsonl", "w", encoding="utf-8") as fh:
                for e in events:
                    fh.write(json.dumps(e) + "\n")

    if args.otlp:
        print("otlp status", post_otlp(to_otlp_json(sessions), args.otlp))

    print(f"sessions={len(sessions)} events={sum(len(s) for s in sessions)} out={out}")
    print("labels.json sha256", hashlib.sha256(labels_bytes).hexdigest())


if __name__ == "__main__":
    main()
