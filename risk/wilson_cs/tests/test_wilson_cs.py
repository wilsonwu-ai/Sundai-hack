import copy
import json
import tempfile
import unittest
from pathlib import Path

from risk.wilson_cs import eval as ev
from risk.wilson_cs.detector import ServiceDetector, featurize
from risk.wilson_cs.tests import fixture


def _split(seed=1, n=800):
    sessions, labels = fixture.generate(n, seed)
    return sessions, labels, ev.make_split(sessions, labels, seed)


def _fitted(group="G3", order=2, n=800):
    sessions, labels, sp = _split(n=n)
    det = ServiceDetector(order=order, group=group)
    det.fit(sp["train"])
    det.calibrate(sp["val"])
    return det, sessions, labels, sp


class OnlineTests(unittest.TestCase):
    def test_later_steps_never_change_earlier_scores(self):
        det, sessions, _, _ = _fitted()
        checked = 0
        for s in sessions[:200]:
            full = det.score_session(s)["steps"]
            for k in range(1, len(s) + 1):
                part = det.score_session(s[:k])["steps"]
                self.assertEqual(part, full[:k])
                checked += 1
        self.assertGreater(checked, 0)

    def test_pressure_mode_never_changes_scores(self):
        det, sessions, _, _ = _fitted()
        for s in sessions[:200]:
            flipped = copy.deepcopy(s)
            for e in flipped:
                e["pressure_mode"] = not e["pressure_mode"]
            dropped = [{k: v for k, v in e.items() if k != "pressure_mode"} for e in s]
            a = det.score_session(s)
            self.assertEqual(a, det.score_session(flipped))
            self.assertEqual(a, det.score_session(dropped))


class FeaturizeTests(unittest.TestCase):
    def test_groups_hide_the_right_fields(self):
        e = fixture._build("service-11", "s", [fixture._read(True)], False)[0]
        e["policy_check"] = "skipped"
        e2 = dict(e, policy_check="passed")
        self.assertEqual(featurize(e, "G0"), featurize(e2, "G0"))
        self.assertNotEqual(featurize(e, "G1"), featurize(e2, "G1"))
        e3 = copy.deepcopy(e)
        e3["params"]["injection_suspected"] = False
        self.assertEqual(featurize(e, "G2"), featurize(e3, "G2"))
        self.assertNotEqual(featurize(e, "G3"), featurize(e3, "G3"))
        txn = fixture._build("service-11", "s", [fixture._txn(True)], False)[0]
        txn2 = copy.deepcopy(txn)
        txn2["params"]["three_ds"] = True
        self.assertEqual(featurize(txn, "G1"), featurize(txn2, "G1"))
        self.assertNotEqual(featurize(txn, "G2"), featurize(txn2, "G2"))

    def test_flags_need_visible_inputs(self):
        sessions, labels = fixture.generate(300, 3)
        bad = next(s for s in sessions if labels[s[0]["session_id"]]["harmful"])
        det0 = ServiceDetector(group="G0").fit(sessions)
        det3 = ServiceDetector(group="G3").fit(sessions)
        f0 = {f for st in det0.score_session(bad)["steps"] for f in st["flags"]}
        f3 = {f for st in det3.score_session(bad)["steps"] for f in st["flags"]}
        self.assertNotIn("skipped_policy", f0)
        self.assertNotIn("injection_then_privileged", f0)
        self.assertIn("skipped_policy", f3)
        self.assertIn("injection_then_privileged", f3)
        self.assertIn("credit_before_verify", f0)


class CalibrationTests(unittest.TestCase):
    def test_budgets_met_on_own_calibration_set(self):
        det, _, _, sp = _fitted()
        val = sp["val"]
        n = len(val)
        paused = logged = 0
        for s in val:
            d = det.score_session(s)["steps"]
            decisions = {st["decision"] for st in d}
            paused += "pause" in decisions
            logged += bool(decisions - {"run"})
        self.assertLessEqual(paused, 0.05 * n)
        self.assertLessEqual(logged, 0.25 * n)


class AgentShapeTests(unittest.TestCase):
    def test_score_agent_shape(self):
        det, sessions, _, _ = _fitted()
        by = {s[0]["session_id"]: s for s in sessions[:20] if s[0]["agent_id"] == sessions[0][0]["agent_id"]}
        out = det.score_agent(sessions[0][0]["agent_id"], by)
        self.assertEqual(set(out), {"agent_id", "risk", "flagged", "paused_sessions", "sessions"})
        self.assertTrue(0.0 <= out["risk"] <= 1.0)
        self.assertIsInstance(out["flagged"], bool)
        for sid, v in out["sessions"].items():
            self.assertIn(v["decision"], {"run", "log", "review", "pause"})
            self.assertEqual(len(v["steps"]), len(by[sid]))
            json.dumps(v)


class EvalTests(unittest.TestCase):
    def test_run_eval_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            res = ev.run_eval(fixture.generate, [1, 2], 500, out_dir=d, hero_fn=fixture.hero_cases)
            for f in ("eval.json", "eval.md", "hero_decisions.json"):
                self.assertTrue((Path(d) / f).exists(), f)
            expected = {"G3_o1", "G3_o2", "G3_o3", "G0_o2", "G1_o2", "G2_o2", "severity_only", "surprise_only"}
            self.assertEqual(set(res["rows"]), expected)
            keys = {"session_auc", "catch_rate", "early_warning", "lead_alert_steps", "lead_pause_steps",
                    "false_alarm_session_rate", "pauses_per_non_harmful", "by_attack", "by_variant",
                    "agent_auc_max", "agent_auc_pause_rate"}
            for row in res["rows"].values():
                self.assertEqual(set(row), {"seeds", "per_seed", "mean"})
                self.assertEqual(set(row["per_seed"]), {"1", "2"})
                self.assertTrue(keys <= set(row["mean"]))
                for m in row["per_seed"].values():
                    self.assertTrue(keys <= set(m))
            hero = json.loads((Path(d) / "hero_decisions.json").read_text())
            self.assertEqual(set(hero["cases"]), {"good_legit", "bad_fake"})
            step = hero["cases"]["bad_fake"]["steps"][0]
            self.assertEqual(set(step), {"step", "action", "risk", "flags", "decision"})
            self.assertIn("G3 minus G0", (Path(d) / "eval.md").read_text())

    def test_behaviorgpt_row_from_rank_file(self):
        sessions, labels, sp = _split(seed=1, n=500)
        ranks = {x[0]["session_id"]: [1 + (i % 3) for i in range(len(x))] for x in sp["val"] + sp["test"]}
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "behaviorgpt_ranks.json").write_text(
                json.dumps({"seed": 1, "catalog_size": 14, "ranks": ranks}))
            res = ev.run_eval(fixture.generate, [1], 500, out_dir=d)
            self.assertIn("behaviorgpt", res["rows"])
            self.assertIn("session_auc", res["rows"]["behaviorgpt"]["mean"])

    def test_auc_basics(self):
        self.assertEqual(ev.auc([1.0], [0.0]), 1.0)
        self.assertEqual(ev.auc([0.5], [0.5]), 0.5)
        self.assertIsNone(ev.auc([], [0.1]))


if __name__ == "__main__":
    unittest.main()
