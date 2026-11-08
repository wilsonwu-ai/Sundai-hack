import json
import unittest
from pathlib import Path

from bank.agents.netsec.toolcalls import CASE_KEYS, generate_toolcalls, toolcall_hero_cases
from risk.wilson_net import toolguard_eval as ev
from risk.wilson_net.toolguard import ToolGuard

_G = {}


def guard():
    if "g" not in _G:
        train, _ = generate_toolcalls(800, 101, mix={"normal": 1.0})
        _G["g"] = ToolGuard().fit(train)
    return _G["g"]


EXPECT = {
    "hero_legit": ("allow", None),
    "hero_attack": ("block_page", 2),
    "bot_campaign": ("block_page", 2),
    "spoofed_agent": ("block_page", 1),
    "key_exfil": ("block_page", 2),
    "slow_drip_lookalike": ("block_page", 1),
    "email_not_on_file": ("hold", 2),
}


class DecisionTests(unittest.TestCase):
    def test_seven_cases_match_the_contract(self):
        g = guard()
        for key, calls in toolcall_hero_cases().items():
            res = g.score_session(calls)
            first = next((r for r in res if r["decision"] in ("hold", "block_page")), None)
            want, idx = EXPECT[key]
            if idx is None:
                self.assertIsNone(first, key)
                self.assertTrue(all(r["decision"] in ("allow", "watch") for r in res))
            else:
                self.assertEqual((first["decision"], first["call_index"]), (want, idx), key)

    def test_exposure_numbers(self):
        g = guard()
        h = toolcall_hero_cases()
        blk = lambda k: next(r for r in g.score_session(h[k]) if r["decision"] != "allow" and r["decision"] != "watch")
        self.assertEqual(blk("hero_attack")["exposure_usd"], 150.0)
        self.assertEqual(blk("spoofed_agent")["exposure_usd"], 60000.0)
        self.assertEqual(blk("key_exfil")["exposure_usd"], 1_000_000.0)
        self.assertEqual(blk("hero_attack")["score"], 1.0)
        self.assertEqual(blk("email_not_on_file")["score"], 0.7)

    def test_legit_kyc_ssn_to_vendor_is_allowed(self):
        g = guard()
        call = toolcall_hero_cases()["hero_legit"][1]
        self.assertEqual(call["bank.args.classes"], ["ssn"])
        r = g.score_call(call, toolcall_hero_cases()["hero_legit"][:1])
        self.assertEqual(r["decision"], "allow")
        self.assertEqual(r["exposure_usd"], 0.0)

    def test_lookalike_host_blocked_at_first_drip_call(self):
        calls = toolcall_hero_cases()["slow_drip_lookalike"]
        first_post = next(i for i, c in enumerate(calls) if c["gen_ai.tool.name"] == "http_post")
        r = guard().score_session(calls)[first_post]
        self.assertEqual(r["decision"], "block_page")

    def test_unattested_privileged_tool_is_blocked(self):
        c = dict(toolcall_hero_cases()["hero_legit"][2])
        c["bank.agent.attested"] = False
        self.assertEqual(guard().score_call(c, [])["decision"], "block_page")
        c["bank.agent.attested"] = True
        self.assertNotEqual(guard().score_call(c, [])["decision"], "block_page")

    def test_markov_alone_never_blocks(self):
        g = guard()
        s, _ = generate_toolcalls(300, 5)
        for sess in s:
            for r in g.score_session(sess):
                if r["decision"] == "watch":
                    self.assertLessEqual(r["score"], 0.69 + 1e-9)
                    self.assertGreaterEqual(r["markov_risk"], g.watch_line)

    def test_guard_ignores_labels_and_raw_values(self):
        c = toolcall_hero_cases()["hero_attack"][2]
        extra = dict(c, attack="none", actor="none")
        self.assertEqual(guard().score_call(c, []), guard().score_call(extra, []))

    def test_watch_budget_on_normal_validation(self):
        train, _ = generate_toolcalls(800, 101, mix={"normal": 1.0})
        g = ToolGuard().fit(train)
        val = [s for i, s in enumerate(train) if i % 5 == 0]
        watched = sum(any(r["decision"] == "watch" for r in g.score_session(s)) for s in val)
        self.assertLessEqual(watched / len(val), 0.05 + 1e-9)


class OnlineTests(unittest.TestCase):
    def test_call_k_sees_only_calls_0_to_k(self):
        g = guard()
        s, _ = generate_toolcalls(80, 7)
        for sess in s:
            full = g.score_session(sess)
            for k in (1, len(sess) // 2 or 1, len(sess)):
                self.assertEqual(g.score_session(sess[:k]), full[:k])

    def test_later_calls_do_not_change_earlier_score_call(self):
        g = guard()
        calls = toolcall_hero_cases()["hero_attack"]
        a = g.score_call(calls[2], calls[:2])
        b = g.score_call(calls[2], calls[:2] + [])
        self.assertEqual(a, b)


class AgentScoreTests(unittest.TestCase):
    def test_agent_gets_max_of_last_50_call_scores(self):
        g = guard()
        h = toolcall_hero_cases()["hero_attack"]
        bad = h[:3]  # call 2 is blocked, score 1.0
        agent = bad[0]["gen_ai.agent.id"]
        self.assertEqual(g.agent_scores([bad])[agent], 1.0)
        # 50 ordinary calls afterwards push the attack out of the window
        ordinary = [dict(h[0], **{"gen_ai.tool.call.id": f"call_{i:04d}"}) for i in range(50)]
        sessions = [bad, ordinary[:25], ordinary[25:]]
        scores = g.agent_scores(sessions)
        self.assertLess(scores[agent], 1.0)
        # 49 ordinary calls leave the attack inside the window
        self.assertEqual(g.agent_scores([bad, ordinary[:49]])[agent], 1.0)

    def test_agents_are_scored_separately(self):
        g = guard()
        h = toolcall_hero_cases()
        scores = g.agent_scores([h["hero_legit"], h["hero_attack"]])
        self.assertEqual(scores["service-58"], 1.0)
        self.assertLess(scores["kyc-07"], 0.7)


class EvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res, cls.guard = ev.run([1], 400)

    def test_json_shape(self):
        r = self.res
        for k in ("sample", "seeds", "sessions_per_seed", "rows", "by_case", "hero", "cases", "agents"):
            self.assertIn(k, r)
        self.assertEqual(set(r["rows"]), {"guard", "http_layer_only", "keyword_screen"})
        keys = {"blocked_before_run", "false_blocks", "legit_sensitive_blocked", "pages_per_1000", "exposure_stopped_usd", "exposure_missed_usd"}
        for row in r["rows"].values():
            self.assertEqual(set(row), keys)
            for m in ("blocked_before_run", "false_blocks", "legit_sensitive_blocked"):
                self.assertEqual(set(row[m]), {"k", "n", "rate"})
        self.assertEqual(set(r["cases"]), set(CASE_KEYS))
        for c in r["cases"].values():
            self.assertEqual(set(c), {"decision", "exposure_usd", "actor", "first_block_index"})
        self.assertEqual(set(r["hero"]), {"hero_legit", "hero_attack"})
        for a in r["agents"]:
            self.assertEqual(set(a), {"agent_id", "agent_type", "score", "hijacked"})
        json.dumps(r)

    def test_http_layer_misses_non_campaign_attacks(self):
        sessions, labels = generate_toolcalls(400, 1)
        dec = ev._http_method(None, sessions, labels)
        for s, (d, _) in zip(sessions, dec):
            lab = labels[s[0]["gen_ai.conversation.id"]]
            if lab["attack"] not in ("none", "bot_campaign"):
                self.assertTrue(all(x == "allow" for x in d))

    def test_pages_dedupe_by_host(self):
        sessions, labels = generate_toolcalls(300, 2, mix={"normal": 0.5, "bot_campaign": 0.5})
        g = guard()
        dec = ev._guard_method(g, sessions, labels)
        blocked_sessions = sum(1 for _, p in dec if p)
        pages = set().union(*(p for _, p in dec))
        self.assertGreater(blocked_sessions, 20)
        self.assertEqual(pages, {"paste.attacker.example"})
        res = ev._evaluate(dec, sessions, labels)
        self.assertEqual(res["pages"], 1)

    def test_hijacked_comes_from_labels_after_scoring(self):
        sessions, labels = generate_toolcalls(400, 1)
        bad = {s[0]["gen_ai.agent.id"] for s in sessions if labels[s[0]["gen_ai.conversation.id"]]["attack"] != "none"}
        self.assertEqual({a["agent_id"] for a in self.res["agents"] if a["hijacked"]}, bad)

    def test_no_raw_ssn_in_results(self):
        import re
        blob = json.dumps(self.res)
        self.assertEqual(re.findall(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)", blob), [])


if __name__ == "__main__":
    unittest.main()
