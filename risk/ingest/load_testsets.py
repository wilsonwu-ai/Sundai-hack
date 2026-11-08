"""Load the bank team's blind test sets into the monitor, one test organisation per set.

    python -m risk.ingest.load_testsets                          # sets A-E, to localhost:4318
    python -m risk.ingest.load_testsets set_B --endpoint http://host:4318

Each set becomes the organisation "Test set A", "Test set B"..., marked as a test
org first: the monitor scores it like any other, but its risky steps are not
queued for a person (nobody can act on a past run), and the dashboard keeps it
out of the live totals. The dashboard's Blind test page opens its sessions there.
Loading a set twice stores it once.
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request

from risk.api.blindtest import SETS, tenant_name
from risk.ingest.load_logs import read_events

CHUNK = 500


def call(method: str, url: str, body) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method=method,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read())


def load(set_name: str, endpoint: str) -> int:
    tenant = tenant_name(set_name)
    quoted = urllib.parse.quote(tenant, safe="")
    call("PUT", f"{endpoint}/tenants/{quoted}", {"kind": "test"})
    events = read_events(SETS / set_name)
    for i in range(0, len(events), CHUNK):
        call("POST", f"{endpoint}/events?tenant={quoted}", events[i:i + CHUNK])
    return len(events)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sets", nargs="*", help="set_A ... (default: every set in bank/testsets/blind)")
    parser.add_argument("--endpoint", default="http://localhost:4318")
    args = parser.parse_args()
    names = args.sets or sorted(p.name for p in SETS.iterdir() if p.is_dir())
    for name in names:
        name = name if name.startswith("set_") else f"set_{name.upper()}"
        try:
            n = load(name, args.endpoint.rstrip("/"))
        except OSError as exc:
            sys.exit(f"{name}: {exc}")
        print(f"{name}: {n} events -> {tenant_name(name)}")
    print("Scoring runs in the monitor; the dashboard's Blind test page links to each session.")


if __name__ == "__main__":
    main()
