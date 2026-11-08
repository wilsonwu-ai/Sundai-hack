"""Where each category's sessions come from, as one shape: CategoryData.

source is one of: generated (agent 9's generator), stand_in (risk/fleet/simulate.py),
team_logs (--logs DIR), missing (the category's module is not built yet).

Test sessions are returned already stripped of pressure_mode. The variant labels travel in a
Sealed object whose contents only open() reads, and the scorecard calls open() after scoring.
The practice set (train and validation) is assembled from known-good runs; that is the only
place a label is used before scoring, the way the team's practice fitting drops pressure runs.
"""

from __future__ import annotations

import importlib
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from risk.fleet import simulate
from risk.fleet.generic import GenericDetector
from risk.ingest.load_logs import read_events, strip_label

SERVICE_FLEET = {"n_good": 8, "n_bad": 2}
NETSEC_FLEET = {"n_good": 4, "n_bad": 1}
PRACTICE_SESSIONS = 360          # stand-in practice set per category
CATEGORY_ORDER = ("service", "netsec", *simulate.TEAM_CATEGORIES)


class Sealed:
    """Variant labels per agent_id. Not read until open() is called."""

    def __init__(self, variants: dict[str, str] | None):
        self._variants = None if variants is None else dict(variants)

    def has_labels(self) -> bool:
        return self._variants is not None

    def open(self) -> dict[str, str]:
        return dict(self._variants or {})


@dataclass
class CategoryData:
    category: str
    source: str
    train: list = field(default_factory=list)
    val: list = field(default_factory=list)
    test: list = field(default_factory=list)       # stripped events
    sealed: Sealed = field(default_factory=lambda: Sealed(None))
    detector_factory: object = None
    note: str = ""


def _split_normal(sessions: list, seed: int, share_train: float = 0.7):
    rng = random.Random(f"practice-split/{seed}")
    pool = list(sessions)
    rng.shuffle(pool)
    n = int(len(pool) * share_train)
    return pool[:n], pool[n:]


def _stripped(sessions):
    return [[strip_label(e) for e in s] for s in sessions]


def _generic_factory():
    return GenericDetector()


# ---- service (agent 9) -------------------------------------------------------------

def load_service(seed: int, sessions_per_agent: int) -> CategoryData:
    from bank.agents.customer_service.generate import generate
    from risk.wilson_cs.detector import ServiceDetector
    from risk.wilson_cs.eval import make_split

    n_agents = SERVICE_FLEET["n_good"] + SERVICE_FLEET["n_bad"]
    practice, plabels = generate(1500, 10_000 + seed, **SERVICE_FLEET)
    split = make_split(practice, plabels, seed)  # train and val: good agents, legit customers
    test, tlabels = generate(sessions_per_agent * n_agents, seed, **SERVICE_FLEET)
    variants = {}
    for lab in tlabels.values():
        variants[lab["agent_id"]] = "weaker" if lab["agent_variant"] == "bad" else "normal"
    return CategoryData(
        "service", "generated", _stripped(split["train"]), _stripped(split["val"]), _stripped(test),
        Sealed(variants), lambda: ServiceDetector(order=2, group="G3"),
        "agent 9's generator and ServiceDetector(order=2, group=G3)",
    )


# ---- netsec (agent 10), lights up when both modules exist ---------------------------------

def load_netsec(seed: int, sessions_per_agent: int) -> CategoryData:
    """Agent 10: AgentScorer fitted on good-agent practice sessions on any traffic (attacks included, as in
    risk/wilson_net/eval.py), calibrated on good-agent validation sessions."""
    try:
        gen_mod = importlib.import_module("bank.agents.netsec.generate")
        scorer = importlib.import_module("risk.wilson_net.agent_scorer")
        detector_cls = scorer.AgentScorer
        n_agents = NETSEC_FLEET["n_good"] + NETSEC_FLEET["n_bad"]
        practice = gen_mod.generate(900, 10_000 + seed, **NETSEC_FLEET)
        plabels = practice["netsec_labels"]
        good = [s for s in practice["netsec"]
                if plabels[s[0]["session_id"]].get("agent_variant") == "good"]  # any traffic: a good agent revoking a stolen token is routine
        train, val = _split_normal(good, seed)
        made = gen_mod.generate(sessions_per_agent * n_agents, seed, **NETSEC_FLEET)
        test, tlabels = made["netsec"], made["netsec_labels"]
        variants = {lab["agent_id"]: ("weaker" if lab["agent_variant"] == "bad" else "normal")
                    for lab in tlabels.values()}
    except Exception as exc:  # the modules are being built; expect "missing" until they land
        return CategoryData("netsec", "missing", note=f"not built yet ({type(exc).__name__}: {exc})")
    return CategoryData("netsec", "generated", _stripped(train), _stripped(val), _stripped(test),
                        Sealed(variants), detector_cls, "agent 10's generator and AgentScorer")


# ---- the team's eight categories -------------------------------------------------------

def load_stand_in(category: str, seed: int, sessions_per_agent: int) -> CategoryData:
    test, sealed = simulate.simulate_fleet(category, seed, sessions_per_agent)
    practice = simulate.simulate_practice(category, 10_000 + seed, PRACTICE_SESSIONS)
    train, val = _split_normal(practice, seed)
    return CategoryData(category, "stand_in", _stripped(train), _stripped(val), _stripped(test), Sealed(sealed["agents"]),
                        _generic_factory, "risk/fleet/simulate.py stand-in with the generic scorer")


def load_team_logs(logs_dir, seeds_for: tuple[str, ...] = simulate.TEAM_CATEGORIES, seed: int = 1) -> dict[str, CategoryData]:
    """Real logs for the team's categories that appear in `logs_dir`. Their sessions carry no
    variant labels, so the reveal does not cover them. The non-pressure sessions are split into
    practice (train, validation); the rest, with every pressure session, is the test set."""
    by_cat = defaultdict(lambda: defaultdict(list))
    for e in read_events(Path(logs_dir)):
        if e["agent_type"] in seeds_for:
            by_cat[e["agent_type"]][e["session_id"]].append(e)
    out = {}
    for category, sessions in by_cat.items():
        ordered = [sorted(ev, key=lambda e: e["step"]) for _, ev in sorted(sessions.items())]
        normal = [s for s in ordered if not s[0]["pressure_mode"]]
        rng = random.Random(f"team-logs/{category}/{seed}")
        pool = list(normal)
        rng.shuffle(pool)
        n_train = int(0.6 * len(pool))
        train, val = pool[:n_train], pool[n_train:n_train + int(0.2 * len(pool))]
        used = {s[0]["session_id"] for s in train + val}
        test = [s for s in ordered if s[0]["session_id"] not in used]
        out[category] = CategoryData(category, "team_logs", _stripped(train), _stripped(val), _stripped(test),
                                     Sealed(None), _generic_factory, "team logs, generic scorer")
    return out


def load_all(seed: int, sessions_per_agent: int, logs_dir=None) -> list[CategoryData]:
    team = load_team_logs(logs_dir, seed=seed) if logs_dir else {}
    out = []
    for category in CATEGORY_ORDER:
        if category == "service":
            out.append(load_service(seed, sessions_per_agent))
        elif category == "netsec":
            out.append(load_netsec(seed, sessions_per_agent))
        elif category in team and team[category].test:
            out.append(team[category])
        else:
            out.append(load_stand_in(category, seed, sessions_per_agent))
    return out
