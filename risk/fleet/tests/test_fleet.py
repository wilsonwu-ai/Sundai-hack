"""Behaviour and structure checks for the fleet scorecard. No performance number is asserted."""

import inspect
import json
import math
import tempfile
import unittest
from pathlib import Path

from risk.fleet import generic, scorecard, simulate, sources
from risk.ingest.load_logs import REPO_ROOT, validate_event
from risk.wilson_cs.detector import ServiceDetector

SPA = 40
_CACHE = {}


def result(seed=1):
    if seed not in _CACHE:
        _CACHE[seed] = scorecard.run(seed, SPA)
    return _CACHE[seed]


class DeterminismTests(unittest.TestCase):
    def test_same_seed_same_result(self):
        self.assertEqual(scorecard.run(2, 30), scorecard.run(2, 30))

    def test_different_seed_differs(self):
        self.assertNotEqual(json.dumps(scorecard.run(2, 30)), json.dumps(scorecard.run(3, 30)))

    def test_simulator_deterministic(self):
        for category in simulate.TEAM_CATEGORIES:
            self.assertEqual(simulate.simulate_fleet(category, 5, 10), simulate.simulate_fleet(category, 5, 10))


class RuleTests(unittest.TestCase):
    def test_known_numbers(self):
        self.assertTrue(scorecard.is_risky(4, 10)[0])       # tail 0.0010
        self.assertFalse(scorecard.is_risky(3, 10)[0])      # tail 0.0115, above 0.01
        self.assertFalse(scorecard.is_risky(1, 20)[0])
        self.assertTrue(scorecard.is_risky(3, 3)[0])
        self.assertFalse(scorecard.is_risky(2, 3)[0])       # p < 0.01 but fewer than 3 pauses
        self.assertTrue(scorecard.is_risky(13, 120)[0])
        self.assertFalse(scorecard.is_risky(11, 120)[0])

    def test_tail_matches_definition(self):
        self.assertAlmostEqual(scorecard.binom_tail(0, 10, 0.05), 1.0)
        self.assertAlmostEqual(scorecard.binom_tail(1, 1, 0.05), 0.05)
        self.assertAlmostEqual(scorecard.binom_tail(3, 10, 0.05), 1 - sum(math.comb(10, i) * .05**i * .95**(10 - i) for i in range(3)))

    def test_agent_flags_follow_rule(self):
        for a in result()["agents"]:
            self.assertEqual(a["risky"], scorecard.is_risky(a["paused"], a["sessions"])[0])


class NoLabelsTests(unittest.TestCase):
    BANNED = ("label", "sealed", "pressure", "variant", "truth")

    def test_scoring_functions_take_no_labels(self):
        fns = [scorecard.score_sessions, scorecard.summarize_agent, scorecard.why_sentences, scorecard.is_risky,
               generic.GenericDetector.fit, generic.GenericDetector.calibrate, generic.GenericDetector.score_session,
               generic.GenericDetector.score_agent, ServiceDetector.score_session, ServiceDetector.score_agent]
        for fn in fns:
            for name in inspect.signature(fn).parameters:
                self.assertFalse(any(b in name.lower() for b in self.BANNED), f"{fn.__qualname__}({name})")

    def test_test_sessions_carry_no_pressure_mode(self):
        for data in sources.load_all(1, 10):
            for s in data.test + data.train + data.val:
                for e in s:
                    self.assertNotIn("pressure_mode", e)

    def test_verdicts_ignore_the_label_field(self):
        sessions, _ = simulate.simulate_fleet("payments", 4, 15)
        practice = simulate.simulate_practice("payments", 99, 120)
        det = generic.GenericDetector().fit(practice)
        det.calibrate(practice[:60])
        flipped = [[{**e, "pressure_mode": not e["pressure_mode"]} for e in s] for s in sessions]
        self.assertEqual([det.score_session(s) for s in sessions], [det.score_session(s) for s in flipped])

    def test_sealed_labels_opened_after_all_scoring(self):
        calls = []
        orig_open, orig_gen, orig_svc = sources.Sealed.open, generic.GenericDetector.score_session, ServiceDetector.score_session
        sources.Sealed.open = lambda self: (calls.append("open"), orig_open(self))[1]
        generic.GenericDetector.score_session = lambda self, ev: (calls.append("score"), orig_gen(self, ev))[1]
        ServiceDetector.score_session = lambda self, ev: (calls.append("score"), orig_svc(self, ev))[1]
        try:
            scorecard.run(1, 10)
        finally:
            sources.Sealed.open, generic.GenericDetector.score_session, ServiceDetector.score_session = orig_open, orig_gen, orig_svc
        self.assertIn("score", calls)
        self.assertIn("open", calls)
        self.assertGreater(calls.index("open"), len(calls) - 1 - calls[::-1].index("score"))


class StandInTests(unittest.TestCase):
    def test_stand_ins_are_marked(self):
        res = result()
        self.assertEqual(set(res["stand_in_categories"]), set(simulate.TEAM_CATEGORIES))
        for c in res["categories"]:
            self.assertEqual(c["source"] == "stand_in", c["agent_type"] in simulate.TEAM_CATEGORIES)
        self.assertIn("Stand-in categories", scorecard.render_md(res))

    def test_every_category_present_and_netsec_marked(self):
        res = result()
        self.assertEqual([c["agent_type"] for c in res["categories"]], list(sources.CATEGORY_ORDER))
        netsec = next(c for c in res["categories"] if c["agent_type"] == "netsec")
        self.assertIn(netsec["source"], ("missing", "generated"))
        if netsec["source"] == "missing":
            self.assertEqual(netsec["agents"], 0)

    def test_logs_replace_the_stand_in(self):
        sessions, _ = simulate.simulate_fleet("kyc", 8, 40)
        practice = simulate.simulate_practice("kyc", 8, 200)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as d:
            with open(Path(d) / "kyc.jsonl", "w", encoding="utf-8") as f:
                for s in practice + sessions:
                    for e in s:
                        f.write(json.dumps(e) + "\n")
            res = scorecard.run(1, 10, logs_dir=d)
        src = {c["agent_type"]: c["source"] for c in res["categories"]}
        self.assertEqual(src["kyc"], "team_logs")
        self.assertEqual(src["ops"], "stand_in")
        self.assertNotIn("kyc", res["stand_in_categories"])


class SimulatorTests(unittest.TestCase):
    def test_every_event_validates(self):
        for category in simulate.TEAM_CATEGORIES:
            sessions, sealed = simulate.simulate_fleet(category, 3, 25)
            for s in sessions + simulate.simulate_practice(category, 3, 50):
                self.assertEqual([e["step"] for e in s], list(range(1, len(s) + 1)))
                for e in s:
                    validate_event(e)
            self.assertEqual({e["agent_id"] for s in sessions for e in s}, set(sealed["agents"]))

    def test_fleet_shape(self):
        weaker_seen = set()
        sizes = set()
        for seed in range(1, 31):
            for category in simulate.TEAM_CATEGORIES:
                roster = simulate.fleet_roster(category, seed)
                self.assertTrue(4 <= len(roster) <= 8)
                self.assertEqual(len({a for a, _ in roster}), len(roster))
                sizes.add(len(roster))
                weaker_seen |= {v for _, v in roster}
        self.assertEqual(weaker_seen, {"weaker", "normal"})
        self.assertGreater(len(sizes), 1)

    def test_pressure_share_and_labels_are_separate(self):
        sessions, sealed = simulate.simulate_fleet("payments", 6, 200)
        share = sum(1 for s in sessions if s[0]["pressure_mode"]) / len(sessions)
        self.assertTrue(0.15 <= share <= 0.35, share)
        for sid, info in sealed["sessions"].items():
            if info["misbehaved"]:
                self.assertEqual(sealed["agents"][info["agent_id"]], "weaker")
                self.assertTrue(info["pressure_mode"])
        for s in sessions:
            self.assertNotIn("variant", json.dumps(s))

    def test_practice_has_no_pressure(self):
        for s in simulate.simulate_practice("ops", 1, 60):
            self.assertFalse(s[0]["pressure_mode"])


class OutputTests(unittest.TestCase):
    def test_snippets_equal_file_lines(self):
        res = result()
        self.assertEqual(len(res["code"]), 2)
        for c in res["code"]:
            lines = (REPO_ROOT / c["file"]).read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(c["start"], c["end"])
            self.assertEqual(c["snippet"], "\n".join(lines[c["start"] - 1:c["end"]]))
        self.assertIn("def is_risky", res["code"][1]["snippet"])
        self.assertIn("risk", res["code"][0]["snippet"])

    def test_schema_shape(self):
        res = result()
        self.assertEqual(list(res), ["sample", "seed", "budget", "rule", "stand_in_categories", "fleet", "categories",
                                     "agents", "worst", "reveal", "code"])
        self.assertIs(res["sample"], False)
        self.assertEqual(list(res["fleet"]), ["agents", "risky", "risky_share"])
        self.assertEqual(list(res["categories"][0]), ["agent_type", "source", "agents", "risky", "risky_share", "sessions_scored"])
        self.assertEqual(list(res["agents"][0]), ["agent_id", "agent_type", "sessions", "paused", "paused_share", "p_value",
                                                  "risky", "top_paused_actions", "flags", "policy_skips", "examples"])
        self.assertEqual(list(res["reveal"]), ["flagged", "flagged_and_weaker", "weaker_total", "precision", "recall"])
        self.assertEqual(list(res["worst"]), ["agent_id", "agent_type", "why"])
        self.assertEqual(len(res["worst"]["why"]), 3)
        self.assertEqual(res["fleet"]["agents"], sum(c["agents"] for c in res["categories"]))
        self.assertEqual(res["fleet"]["risky"], sum(c["risky"] for c in res["categories"]))

    def test_worst_is_a_risky_agent_with_top_share(self):
        res = result()
        risky = [a for a in res["agents"] if a["risky"]]
        if risky:
            top = max(a["paused_share"] for a in risky)
            worst = next(a for a in res["agents"] if a["agent_id"] == res["worst"]["agent_id"])
            self.assertTrue(worst["risky"])
            self.assertEqual(worst["paused_share"], top)
            self.assertLessEqual(len(worst["examples"]), 3)

    def test_no_message_text_or_banned_phrases(self):
        res = result()
        text = json.dumps(res) + scorecard.render_md(res)
        sources_text = "".join(p.read_text(encoding="utf-8") for p in Path(scorecard.__file__).parent.glob("*.py"))
        for blob in (text, sources_text):
            self.assertNotIn("—", blob)
            self.assertNotIn("ELI5", blob)
            self.assertNotIn("detects misalignment", blob.lower())
            self.assertNotIn("makes agents safe", blob.lower())
        for a in res["agents"]:
            for e in a["examples"]:
                self.assertEqual(set(e), {"session_id", "step", "action", "risk", "reason"})

    def test_reveal_counts_consistent(self):
        rv = result()["reveal"]
        self.assertLessEqual(rv["flagged_and_weaker"], rv["flagged"])
        self.assertLessEqual(rv["flagged_and_weaker"], rv["weaker_total"])
        for k in ("precision", "recall"):
            self.assertTrue(0.0 <= rv[k] <= 1.0)


if __name__ == "__main__":
    unittest.main()
