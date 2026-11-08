"""Assets, value at risk per request, and the frozen triage rule.

The dollar values are illustrative assumptions for the demo bank (see
docs/network-agent.md). When bank/agents/netsec/assets.json exists it is read
(tolerantly) and overlays the built-in copy of the contract's table.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

PAGE_EXPOSURE_USD = 250_000
PER_RECORD_USD = 150
PER_ACCOUNT_USD = 2_500
FIXED_USD = {"internal_network": 1_000_000, "admin": 5_000_000, "exporter": 5_000}
PAGE_ON_LINE_ASSETS = {"internal_network", "admin"}
DEFAULT_PAYEE_EXPECTED_USD = 5_000.0

ROUTE_ASSET = {
    "POST /v1/payees": "payments_core",
    "PATCH /v1/accounts/{account_id}/limits": "payments_core",
    "POST /v1/transfers": "payments_core",
    "POST /v1/credits": "credits",
    "POST /v1/disputes": "credits",
    "GET /v1/customers/{customer_id}": "customer_data",
    "GET /v1/accounts/{account_id}": "customer_data",
    "GET /v1/accounts/{account_id}/transactions": "customer_data",
    "GET /v0/accounts/{account_id}": "customer_data",
    "POST /v1/auth/token": "identity",
    "POST /v1/auth/mfa/verify": "identity",
    "PATCH /v1/customers/{customer_id}": "identity",
    "POST /v1/webhooks": "internal_network",
    "GET /v1/admin/users": "admin",
    "POST /v1/exports": "exporter",
    "GET /v1/me": "reads",
    "GET /v1/accounts": "reads",
    "GET /v1/accounts/{account_id}/balance": "reads",
    "GET /v1/transactions/{txn_id}": "reads",
}

# Routes whose request moves money or puts a balance at risk: transfers, credits,
# limit changes, and a customer-record patch (an account takeover puts the
# average balance at risk). Payee creation does not move money by itself.
MONEY_ROUTES = {
    "POST /v1/transfers",
    "POST /v1/credits",
    "PATCH /v1/accounts/{account_id}/limits",
    "PATCH /v1/customers/{customer_id}",
}

# A fixture copy of the contract's table, in the shape the loader understands.
DEFAULT_ASSETS_JSON = {
    "assets": {
        "payments_core": {"routes": [r for r, a in ROUTE_ASSET.items() if a == "payments_core"]},
        "credits": {"routes": [r for r, a in ROUTE_ASSET.items() if a == "credits"]},
        "customer_data": {"routes": [r for r, a in ROUTE_ASSET.items() if a == "customer_data"], "per_record_usd": PER_RECORD_USD},
        "identity": {"routes": [r for r, a in ROUTE_ASSET.items() if a == "identity"], "per_account_usd": PER_ACCOUNT_USD},
        "internal_network": {"routes": ["POST /v1/webhooks"], "value_at_risk_usd": 1_000_000},
        "admin": {"routes": ["GET /v1/admin/users"], "value_at_risk_usd": 5_000_000},
        "exporter": {"routes": ["POST /v1/exports"], "value_at_risk_usd": 5_000},
        "reads": {"routes": [r for r, a in ROUTE_ASSET.items() if a == "reads"], "value_at_risk_usd": 0},
    }
}

ASSETS_PATH = Path(__file__).resolve().parents[2] / "bank" / "agents" / "netsec" / "assets.json"
_FIXED_KEYS = ("usd_per_unit", "value_at_risk_usd", "value_usd", "fixed_usd", "usd")


def load_assets(path=None) -> dict:
    """Asset table as {route_asset, fixed, per_record_usd, per_account_usd, source}."""
    out = {"route_asset": dict(ROUTE_ASSET), "fixed": dict(FIXED_USD),
           "per_record_usd": PER_RECORD_USD, "per_account_usd": PER_ACCOUNT_USD, "route_severity": {},
           "source": "built-in"}
    p = Path(path) if path else ASSETS_PATH
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return out
    items = data.get("assets", data) if isinstance(data, dict) else {}
    if isinstance(items, list):
        items = {(x.get("name") or x.get("asset")): x for x in items if isinstance(x, dict)}
    if not isinstance(items, dict):
        return out
    used = False
    for name, spec in items.items():
        if not isinstance(spec, dict) or not isinstance(name, str):
            continue
        routes = spec.get("routes")
        if isinstance(routes, list):
            for r in routes:
                if isinstance(r, str):
                    out["route_asset"][r] = name
                    used = True
        if name in out["fixed"]:
            for k in _FIXED_KEYS:
                if isinstance(spec.get(k), (int, float)) and not isinstance(spec.get(k), bool):
                    out["fixed"][name] = spec[k]
                    used = True
                    break
        unit = spec.get("usd_per_unit")
        if isinstance(unit, (int, float)) and not isinstance(unit, bool):
            if spec.get("value_model") == "per_record":
                out["per_record_usd"] = unit
            elif spec.get("value_model") == "per_account":
                out["per_account_usd"] = unit
        if isinstance(spec.get("per_record_usd"), (int, float)):
            out["per_record_usd"] = spec["per_record_usd"]
        if isinstance(spec.get("per_account_usd"), (int, float)):
            out["per_account_usd"] = spec["per_account_usd"]
    sev = data.get("route_severity") if isinstance(data, dict) else None
    if isinstance(sev, dict):
        out["route_severity"] = {k: float(v) for k, v in sev.items()
                                 if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)}
    if used:
        out["source"] = str(p)
    return out


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def is_money_move(route_key: str) -> bool:
    return route_key in MONEY_ROUTES


def request_value(route_key: str, req: dict, assets: dict, full_view: bool, defaults: dict | None = None):
    """(asset, value_at_risk_usd, money_moves, customer_data_exposed) for one request.

    full_view False (metadata groups below N2 cannot see amounts or object
    ownership): amounts fall back to `defaults` (learned on normal training
    traffic) and customer_data_exposed is never true.
    """
    defaults = defaults or {}
    asset = req.get("bank.asset")
    if not isinstance(asset, str):
        asset = assets["route_asset"].get(route_key, "reads")
    status = _num(req.get("http.response.status_code")) or 0
    exposed = bool(full_view and req.get("bank.object_owner_match") is False and 200 <= status < 300)
    value = 0.0
    if asset == "payments_core":
        if route_key == "POST /v1/transfers":
            amt = _num(req.get("bank.amount_usd")) if full_view else None
            value = float(amt) if amt is not None else float(defaults.get(route_key, 0.0))
        elif route_key == "PATCH /v1/accounts/{account_id}/limits":
            lo, hi = _num(req.get("bank.limit_from_usd")), _num(req.get("bank.limit_to_usd"))
            if full_view and lo is not None and hi is not None:
                value = max(0.0, float(hi) - float(lo))
            else:
                value = float(defaults.get(route_key, 0.0))
        else:  # payees: the expected transfer
            value = float(defaults.get("POST /v1/payees", DEFAULT_PAYEE_EXPECTED_USD))
    elif asset == "credits":
        amt = _num(req.get("bank.amount_usd")) if full_view else None
        value = float(amt) if amt is not None else float(defaults.get(route_key, 0.0))
    elif asset == "identity":
        value = float(assets["per_account_usd"])
    elif asset in assets["fixed"]:
        value = float(assets["fixed"][asset])
    if exposed:
        value = max(value, float(assets["per_record_usd"]))
    return asset, value, is_money_move(route_key), exposed


def learn_defaults(priced: list[tuple[str, float]], transfers: list[float]) -> dict:
    """Per-route mean value for the money routes, and the expected payee transfer."""
    by: dict[str, list[float]] = {}
    for rk, v in priced:
        by.setdefault(rk, []).append(v)
    out = {rk: sum(vs) / len(vs) for rk, vs in by.items() if vs}
    out["POST /v1/payees"] = statistics.median(transfers) if transfers else DEFAULT_PAYEE_EXPECTED_USD
    return out


def triage(risk: float, exposure_usd: float, money_moves: bool, customer_data_exposed: bool,
           asset: str, pause_line: float, log_line: float) -> str:
    """The frozen triage rule: allow, log, contain or page_it."""
    if (exposure_usd >= PAGE_EXPOSURE_USD
            or (money_moves and risk >= pause_line)
            or customer_data_exposed
            or (asset in PAGE_ON_LINE_ASSETS and risk >= pause_line)):
        return "page_it"
    if risk >= pause_line:
        return "contain"
    if risk >= log_line:
        return "log"
    return "allow"
