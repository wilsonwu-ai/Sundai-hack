#!/usr/bin/env python3
"""Optional BehaviorGPT scorer for the service risk agent. Written, NOT run by the builder.

No network call happens until you run it yourself. It:

  1. builds a catalog of the 14 service actions (ids `service.<action>`, the same item
     ids risk/behaviorgpt/history.py produces) with plain-English names,
  2. uploads it with the behaviorgpt SDK (or prints the install line if the SDK is missing),
  3. for every step of the chosen sessions, ranks the catalog given the actions so far
     and records the rank of the action the agent actually took (1 = top pick),
  4. writes risk/wilson_cs/results/behaviorgpt_ranks.json, which
     `python3 -m risk.wilson_cs.eval` picks up automatically as a `behaviorgpt` row
     (surprise from the rank, scored on the ranked subset of the test split).

Sessions ranked: for the chosen --seed, the test split (a seeded random sample of
--max-test sessions, so harmful ones are included) plus validation sessions (--max-val,
needed to calibrate). The split is risk.wilson_cs.eval.make_split, so the two scripts agree.

The key is read only from the UNBOXAI_API_KEY environment variable and is never printed;
error text is scrubbed of it.

Exact commands, from the repo root:

    pip install behaviorgpt                 # SDK, only needed for the catalog upload
    export UNBOXAI_API_KEY=...              # free key at https://portal.unboxai.com
    python3 -m risk.wilson_cs.behaviorgpt_rank --seed 1 --sessions 1500 --max-test 150 --max-val 120
    python3 -m risk.wilson_cs.eval --seeds 1 --sessions 1500   # now includes the behaviorgpt row

Re-use an uploaded catalog (skips the upload):

    python3 -m risk.wilson_cs.behaviorgpt_rank --seed 1 --catalog-id <id>

The live API is called like the sibling DEFY worker: POST to the vector recommendations
endpoint with headers x-api-key and x-catalog-id, body {history: [viewItem events],
store_id, domains, timezone, limit, offset, filters, register_event: false}; the ranking
comes back nested as {"products": {"items": [{id, score}, ...]}}, best first. Every
failure branch is logged to stderr without the key. Use the same --sessions and --seed
as the eval run, since the split depends on both.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from risk.behaviorgpt.history import item_id
from risk.wilson_cs.detector import SEVERITY
from risk.wilson_cs.eval import make_split

RESULTS = Path(__file__).resolve().parent / "results"
UPSTREAM_URL = "https://behaviorgpt-northeurope.api.unboxai.com/v1/vector/recommendations"
CATALOG_CSV = RESULTS / "service_actions.csv"
CATALOG_PARQUET = RESULTS / "service_actions.parquet"  # file name becomes the catalog name
OUT = RESULTS / "behaviorgpt_ranks.json"
DOMAINS = {"market": "us", "postal_code": "unk", "gender": "unk", "age_group": "unk"}
CSV_COLUMNS = ["id", "name", "brand", "categories", "image_url", "event_type", "group", "sales_since",
               "timestamp", "frequency", "market", "price", "currency", "search_keywords", "keywords"]

PLAIN = {
    "read_request": "reads the customer's request",
    "answer_question": "answers a general question",
    "send_reply": "sends a reply to the customer",
    "verify_identity": "verifies the customer's identity",
    "lookup_account": "looks up the customer's account",
    "lookup_transaction": "looks up a card transaction",
    "escalate": "escalates the case to the fraud team or a supervisor",
    "deny_request": "denies the customer's request",
    "close_case": "closes the case",
    "block_card": "blocks the customer's card",
    "reissue_card": "reissues the customer's card",
    "open_dispute": "opens a fraud or billing dispute",
    "update_contact": "changes the customer's email, phone or address",
    "issue_credit": "issues a credit to the customer's account",
}
CATALOG_IDS = [f"service.{a}" for a in PLAIN]
assert set(PLAIN) == set(SEVERITY)


def get_key() -> str:
    key = os.environ.get("UNBOXAI_API_KEY", "").strip()
    if not key:
        print("Set UNBOXAI_API_KEY first (free key at https://portal.unboxai.com).", file=sys.stderr)
        sys.exit(1)
    return key


def scrub(text, key: str) -> str:
    return str(text).replace(key, "***")


def write_catalog_csv() -> Path:
    RESULTS.mkdir(exist_ok=True)
    with open(CATALOG_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for action, meaning in PLAIN.items():
            w.writerow({
                "id": f"service.{action}",
                "name": f"{action.replace('_', ' ')}: customer service agent {meaning}",
                "brand": "bank service agent",
                "categories": "Agent action, customer service",
                "event_type": "product",
                "group": "product",
                "price": str(SEVERITY[action]),
                "keywords": f"[customer service, {action.replace('_', ' ')}]",
            })
    return CATALOG_CSV


def upload_catalog(key: str) -> str:
    try:
        from behaviorgpt import UnboxAIClient, catalog_from_csv
    except ImportError:
        print("The behaviorgpt SDK is not installed. Run: pip install behaviorgpt", file=sys.stderr)
        sys.exit(1)
    client = UnboxAIClient(api_key=key, market="us")
    try:
        parquet = catalog_from_csv(str(write_catalog_csv()), str(CATALOG_PARQUET))
        job = client.embed(parquet, wait=True, timeout=1800.0)
    except Exception as err:  # report the reason without echoing the key
        print(f"Upload failed: {type(err).__name__}: {scrub(err, key)}", file=sys.stderr)
        sys.exit(1)
    return job.catalog_id


def rank_next(key: str, catalog_id: str, history: list[str], session: str, tz: str = "UTC") -> list[str]:
    """Catalog ids ranked best first for the next action, given the item ids so far."""
    end = datetime.now(timezone.utc)
    last = len(history) - 1
    events = [{
        "store_id": catalog_id, "domains": DOMAINS, "timezone": tz,
        "timestamp": (end - timedelta(seconds=last - k)).isoformat().replace("+00:00", "Z"),
        "event": {"type": "viewItem", "product": product}, "session_id": session,
    } for k, product in enumerate(history)]
    body = {"history": events, "store_id": catalog_id, "domains": DOMAINS, "timezone": tz,
            "limit": 100, "offset": 0, "filters": {}, "register_event": False}
    req = urllib.request.Request(
        UPSTREAM_URL, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", "x-api-key": key, "x-catalog-id": catalog_id})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.load(res)
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"HTTP {err.code}: {scrub(err.read().decode(errors='replace')[:300], key)}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        raise RuntimeError(f"{type(err).__name__}: {scrub(err, key)}") from None
    page = data.get("products") if isinstance(data.get("products"), dict) else data  # live API nests under "products"
    return [it["id"] for it in page.get("items", []) if isinstance(it, dict) and "id" in it]


def rank_of(item: str, ranked: list[str]) -> int:
    seen = [a for a in ranked if a in CATALOG_IDS]
    return seen.index(item) + 1 if item in seen else len(CATALOG_IDS)  # absent = worst


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--sessions", type=int, default=1500, help="must match the eval run")
    ap.add_argument("--max-test", type=int, default=150)
    ap.add_argument("--max-val", type=int, default=120)
    ap.add_argument("--catalog-id", help="skip the upload and use this catalog id")
    ap.add_argument("--sleep", type=float, default=0.05, help="seconds between calls")
    args = ap.parse_args(argv)

    key = get_key()
    catalog_id = args.catalog_id or upload_catalog(key)
    print(f"catalog id: {catalog_id}")

    gen = importlib.import_module("bank.agents.customer_service.generate").generate
    sessions, labels = gen(args.sessions, args.seed)
    sp = make_split(sessions, labels, args.seed)
    rng = random.Random(args.seed)
    test = rng.sample(sp["test"], min(args.max_test, len(sp["test"])))
    chosen = test + sp["val"][: args.max_val]

    ranks: dict[str, list[int]] = {}
    calls = 0
    for n, session in enumerate(chosen, start=1):
        sid = session[0]["session_id"]
        items = [item_id(e) for e in session]  # item_id never reads pressure_mode
        out = []
        for i, it in enumerate(items):
            try:
                ranked = rank_next(key, catalog_id, items[:i], f"svc-{sid}")
            except Exception as err:
                print(f"rank call failed at {sid} step {i + 1}: {scrub(err, key)}", file=sys.stderr)
                return 1
            out.append(rank_of(it, ranked))
            calls += 1
            time.sleep(args.sleep)
        ranks[sid] = out
        if n % 20 == 0:
            print(f"{n}/{len(chosen)} sessions, {calls} calls")
    RESULTS.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"generated_by": "risk/wilson_cs/behaviorgpt_rank.py", "synthetic": True,
                               "seed": args.seed, "sessions": args.sessions, "catalog_id": catalog_id,
                               "catalog_size": len(CATALOG_IDS), "ranks": ranks}, indent=1))
    print(f"wrote {OUT} ({len(ranks)} sessions, {calls} calls)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
