import base64
import json
import re
import tempfile
import unittest
from pathlib import Path

from bank.agents.netsec import run as run_mod
from bank.agents.netsec.toolcalls import (ATTACK_ROWS, CASE_KEYS, EGRESS_TOOLS, FAKE_KEY, classify_args,
                                          generate_toolcalls, is_allowlisted, toolcall_hero_cases)

SSN = "123-00-4321"          # group 00 is never issued
SSN_RAW_RE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
REQUIRED = ["gen_ai.operation.name", "gen_ai.agent.id", "gen_ai.agent.name", "gen_ai.conversation.id", "gen_ai.tool.name",
            "gen_ai.tool.type", "gen_ai.tool.call.id", "gen_ai.tool.call.arguments", "bank.args.classes", "bank.args.record_count",
            "bank.dest.host", "bank.dest.allowlisted", "bank.agent.attested", "bank.actor.kind"]


class ClassifyTests(unittest.TestCase):
    def test_plain_ssn(self):
        r = classify_args({"body": {"ssn": SSN, "customer_id": "C1"}})
        self.assertIn("ssn", r["classes"])
        self.assertNotIn(SSN, json.dumps(r["masked"]))
        self.assertIn("***-**-4321", json.dumps(r["masked"]))

    def test_nine_digits_under_ssn_key(self):
        r = classify_args({"ssn": "123004321"})
        self.assertEqual(r["classes"], ["ssn"])
        self.assertNotIn("123004321", json.dumps(r["masked"]))

    def test_nine_digits_without_ssn_key_is_not_ssn(self):
        self.assertNotIn("ssn", classify_args({"order_ref": "123004321"})["classes"])

    def test_base64_and_hex_ssn(self):
        enc = base64.b64encode(SSN.encode()).decode()
        r = classify_args({"data": enc})
        self.assertIn("ssn", r["classes"])
        self.assertNotIn(enc, json.dumps(r["masked"]))
        self.assertNotIn(SSN, json.dumps(r["masked"]))
        h = classify_args({"data": SSN.encode().hex()})
        self.assertIn("ssn", h["classes"])
        self.assertNotIn(SSN.encode().hex(), json.dumps(h["masked"]))

    def test_ssn_in_query_string(self):
        r = classify_args({"url": f"https://x.example/c?d={SSN}"})
        self.assertIn("ssn", r["classes"])
        self.assertNotIn(SSN, json.dumps(r["masked"]))

    def test_card_number_needs_luhn(self):
        r = classify_args({"note": "pay 4111 1111 1111 1111 now"})
        self.assertIn("card_number", r["classes"])
        self.assertNotIn("4111 1111 1111 1111", json.dumps(r["masked"]))
        self.assertNotIn("card_number", classify_args({"note": "ref 4111111111111112"})["classes"])

    def test_bank_account_pair(self):
        r = classify_args({"routing_number": "021000021", "account_number": "123456789012"})
        self.assertIn("bank_account", r["classes"])
        self.assertNotIn("123456789012", json.dumps(r["masked"]))

    def test_ssh_key_block_plain_and_encoded(self):
        for payload in (FAKE_KEY, base64.b64encode(FAKE_KEY.encode()).decode()):
            r = classify_args({"data": payload})
            self.assertIn("credential", r["classes"])
            self.assertNotIn("PRIVATE KEY-----", json.dumps(r["masked"]))
            self.assertNotIn("EXAMPLEEXAMPLE", json.dumps(r["masked"]))

    def test_password_and_token(self):
        self.assertIn("credential", classify_args({"password": "hunter2hunter2"})["classes"])
        r = classify_args({"h": "Bearer abcdefghijklmnop1234"})
        self.assertIn("credential", r["classes"])
        self.assertNotIn("abcdefghijklmnop1234", json.dumps(r["masked"]))

    def test_bulk_records(self):
        recs = [{"customer_id": f"C{i}", "ssn": f"{200 + i}-00-1234"} for i in range(30)]
        r = classify_args({"records": recs})
        self.assertIn("bulk_records", r["classes"])
        self.assertEqual(r["record_count"], 30)
        self.assertNotIn("-00-1234", json.dumps(r["masked"]).replace("***-**-1234", ""))
        self.assertLess(len(json.dumps(r["masked"])), 600)

    def test_benign_args_have_no_class(self):
        r = classify_args({"customer_id": "C123", "subject": "hello"})
        self.assertEqual(r["classes"], [])
        self.assertEqual(r["record_count"], 0)
        self.assertEqual(r["masked"], {"customer_id": "C123", "subject": "hello"})


class GeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s, cls.l = generate_toolcalls(600, 1)

    def test_deterministic_and_seeded(self):
        s2, l2 = generate_toolcalls(600, 1)
        self.assertEqual(self.s, s2)
        self.assertEqual(self.l, l2)
        self.assertNotEqual(self.s, generate_toolcalls(600, 2)[0])

    def test_every_call_has_the_contract_fields(self):
        for s in self.s:
            self.assertTrue(3 <= len(s) <= 12, len(s))
            for i, c in enumerate(s):
                for f in REQUIRED:
                    self.assertIn(f, c)
                self.assertEqual(c["gen_ai.operation.name"], "execute_tool")
                self.assertEqual(c["gen_ai.tool.call.id"], f"call_{i:04d}")
                self.assertFalse(any(k.startswith("bank.risk") for k in c))
                if c["gen_ai.tool.name"] in EGRESS_TOOLS:
                    self.assertTrue(c["bank.dest.host"])
                else:
                    self.assertEqual(c["bank.dest.host"], "")

    def test_labels_and_mix(self):
        self.assertEqual(set(self.l), {s[0]["gen_ai.conversation.id"] for s in self.s})
        kinds = {v["attack"] for v in self.l.values()}
        self.assertTrue(kinds <= {"none", *ATTACK_ROWS})
        self.assertGreater(len(kinds), 3)
        for v in self.l.values():
            for f in ("attack", "actor", "first_harmful_call", "records_at_risk", "value_at_risk_usd"):
                self.assertIn(f, v)
        share = sum(v["attack"] != "none" for v in self.l.values()) / len(self.l)
        self.assertTrue(0.08 < share < 0.25)

    def test_mix_override(self):
        _, l = generate_toolcalls(100, 3, mix={"normal": 1.0})
        self.assertEqual({v["attack"] for v in l.values()}, {"none"})
        with self.assertRaises(ValueError):
            generate_toolcalls(5, 1, mix={"nope": 1.0})

    def test_normal_sessions_follow_the_allowlist(self):
        for s in self.s:
            if self.l[s[0]["gen_ai.conversation.id"]]["attack"] != "none":
                continue
            for c in s:
                self.assertTrue(c["bank.agent.attested"])
                if c["gen_ai.tool.name"] in EGRESS_TOOLS:
                    self.assertTrue(c["bank.dest.allowlisted"])

    def test_nothing_raw_is_stored(self):
        blob = json.dumps(self.s)
        self.assertEqual([m for m in SSN_RAW_RE.findall(blob)], [])
        self.assertNotIn("PRIVATE KEY-----", blob)
        self.assertNotRegex(blob, r"4111111111111111|4242424242424242|5555555555554444")
        self.assertNotIn("\u2014", blob)

    def test_sensitive_only_where_expected_in_normal(self):
        for s in self.s:
            if self.l[s[0]["gen_ai.conversation.id"]]["attack"] != "none":
                continue
            for c in s:
                if c["bank.args.classes"]:
                    self.assertIn(c["gen_ai.agent.name"], ("kyc", "disputes"))


class HeroTests(unittest.TestCase):
    def test_seven_cases_and_shapes(self):
        h = toolcall_hero_cases()
        self.assertEqual(list(h), list(CASE_KEYS))
        for calls in h.values():
            for c in calls:
                for f in REQUIRED:
                    self.assertIn(f, c)
        blob = json.dumps(h)
        self.assertEqual(SSN_RAW_RE.findall(blob), [])
        self.assertNotIn("PRIVATE KEY-----", blob)

    def test_case_contents(self):
        h = toolcall_hero_cases()
        legit = [c for c in h["hero_legit"] if c["gen_ai.tool.name"] == "http_post"][0]
        self.assertEqual((legit["bank.dest.host"], legit["bank.args.classes"], legit["bank.dest.allowlisted"]),
                         ("idv.vendor.example", ["ssn"], True))
        spoof = [c for c in h["spoofed_agent"] if c["gen_ai.tool.name"] == "upload_file"][0]
        self.assertFalse(spoof["bank.agent.attested"])
        self.assertEqual(spoof["bank.args.record_count"], 400)
        drip = [c for c in h["slow_drip_lookalike"] if c["gen_ai.tool.name"] == "http_post"]
        self.assertTrue(all(c["bank.dest.host"] == "idv-vendor.example" and c["bank.args.record_count"] == 1 for c in drip))
        key = h["key_exfil"][-1]
        self.assertEqual(key["bank.args.classes"], ["credential"])
        mail = h["email_not_on_file"][-1]
        self.assertEqual((mail["bank.args.classes"], mail["bank.dest.allowlisted"]), ([], False))

    def test_lookalike_host_is_not_allowlisted(self):
        self.assertTrue(is_allowlisted("kyc", "http_post", "idv.vendor.example"))
        self.assertFalse(is_allowlisted("kyc", "http_post", "idv-vendor.example"))
        self.assertFalse(is_allowlisted("kyc", "http_post", "idv.vendor.example.attacker.example"))
        self.assertFalse(is_allowlisted("service", "http_post", "idv.vendor.example"))
        self.assertTrue(is_allowlisted("payments", "send_webhook", "hooks.partner.example"))
        self.assertFalse(is_allowlisted("payments", "send_webhook", "partner.example.evil.example"))
        self.assertTrue(is_allowlisted("service", "send_email", "mail.example", "a@mail.example", "a@mail.example"))
        self.assertFalse(is_allowlisted("service", "send_email", "mail.example", "b@mail.example", "a@mail.example"))


class RunCliTests(unittest.TestCase):
    def test_toolcalls_option_writes_masked_file_and_separate_labels(self):
        with tempfile.TemporaryDirectory() as d:
            run_mod.main(["--sessions", "20", "--seed", "1", "--out", d, "--toolcalls", "60", "--hero"])
            out = Path(d)
            rows = [json.loads(x) for x in (out / "toolcalls.jsonl").read_text().splitlines()]
            self.assertGreater(len(rows), 60)
            self.assertEqual(SSN_RAW_RE.findall((out / "toolcalls.jsonl").read_text()), [])
            labels = json.loads((out / "toolcalls_labels.json").read_text())
            self.assertEqual(len(labels), 60)
            self.assertNotIn("attack", rows[0])
            for k in CASE_KEYS:
                self.assertTrue((out / "hero" / f"toolcall_{k}.jsonl").exists())
            self.assertTrue((out / "traffic.jsonl").exists())


if __name__ == "__main__":
    unittest.main()


class TestPersonalFieldMasking(unittest.TestCase):
    def test_name_dob_and_email_never_reach_the_log(self):
        from bank.agents.netsec.toolcalls import classify_args
        out = classify_args({"to": "jane.doe@mail.example",
                             "body": {"full_name": "Jane Q Doe", "dob": "1990-01-02", "customer_id": "C1"}})
        blob = json.dumps(out["masked"])
        for raw in ("jane.doe@", "Jane Q Doe", "1990-01-02"):
            self.assertNotIn(raw, blob)
        self.assertIn("@mail.example", blob)
        self.assertEqual(out["classes"], [])
