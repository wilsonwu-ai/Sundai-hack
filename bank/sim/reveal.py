"""Reveal the answer for one blind test set and prove it was not changed after the seal.

    python -m bank.sim.reveal A            # or set_A
    python -m bank.sim.reveal --all

Reads the answer key from logs/answer_keys/ (kept off git) and checks its SHA-256 against the line committed in
bank/testsets/seals.txt. Exit code 0 = seal matches, 1 = mismatch, 2 = missing file or bad argument.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEYS = ROOT / "logs" / "answer_keys"
SEALS = ROOT / "bank" / "testsets" / "seals.txt"


def read_seals(path: Path = SEALS) -> dict[str, str]:
    seals = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            name, digest = line.split()
            seals[name] = digest
    return seals


def set_name(arg: str) -> str:
    arg = arg.strip()
    letter = arg[4:] if arg.lower().startswith("set_") else arg
    if len(letter) != 1 or not letter.isalpha():
        raise ValueError(f"not a set letter: {arg!r}")
    return f"set_{letter.upper()}"


def reveal(name: str, keys: Path = KEYS, seals_path: Path = SEALS) -> tuple[bool, str]:
    """(seal matches, report text) for one set."""
    key_path = keys / f"{name}_labels.json"
    raw = key_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    sealed = read_seals(seals_path).get(name)
    if sealed is None:
        raise KeyError(f"{name} is not in {seals_path}")
    ok = digest == sealed

    labels = json.loads(raw)
    sessions = labels["session_labels"]
    lines = [f"{name}"]
    mapping_path = keys / "mapping.json"
    info = json.loads(mapping_path.read_text())["sets"].get(name, {}) if mapping_path.exists() else {}
    if info:
        label = f"{info['scenario']}: " if info.get("scenario") is not None else ""
        lines.append(f"  scenario       {label}{info.get('scenario_name', '')}  (seed {info.get('seed')})")
    if not labels["drifters"]:
        lines.append("  rogue agents   none (all 8 aligned)")
    for d in labels["drifters"]:
        first = next((s for s, bad in sessions.items() if bad and f"_{d['agent_type']}_" in s), None)
        lines.append(f"  rogue agent    {d['agent_id']:<16} from session index {d['drift_start_session']} "
                     f"(first rogue session {first}), drift level {d['drift_level']}")
    lines.append(f"  rogue sessions {sum(sessions.values())} of {len(sessions)}")
    lines.append(f"  sha256         {digest}")
    lines.append(f"  sealed         {sealed}")
    lines.append(f"  seal check     {'OK - answer key unchanged since the seal' if ok else 'MISMATCH - answer key changed'}")
    return ok, "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("set", nargs="?", help="set letter, e.g. A or set_A")
    p.add_argument("--all", action="store_true", help="reveal every set in seals.txt")
    args = p.parse_args(argv)
    try:
        names = sorted(read_seals()) if args.all else [set_name(args.set or "")]
        results = [reveal(n) for n in names]
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("\n\n".join(text for _, text in results))
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
