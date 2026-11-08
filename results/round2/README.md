# Blind test round 2 (sets F to O): hand-in

Scored before the answers were opened. Every score file records `labels_read: false` and the model commit it was
scored with, with no uncommitted changes. The model and the hand-in rule were chosen on our own simulator runs and
the revealed sets A-E only ([validation](validation/README.md)); sets F-O were not read before commit `65b45b1`.

- **Round-2 model** (commit `65b45b1`): `scores_set_*.json`, hand-in `handin.json`.
- **Round-1 model** (commit `62a1511`) with the round-1 rule, for comparison: `round1_model/`.

## Hand-in

| Set | Rogue agent | First rogue session | Why called | Round-1 model's call |
|---|---|---|---|---|
| F | `payments-01` | `s_bbb304_payments_017` (session 17) | above its type | not called |
| F | `sales-01` | `s_bbb304_sales_014` (session 14) | above its type | not called |
| G | `ops-01` | `s_4d1097_ops_014` (session 14) | jump and above its type | same, from session 14 |
| G | `sales-01` | `s_4d1097_sales_017` (session 17) | above its type | not called |
| H | `collections-01` | `s_c2307b_collections_009` (session 9) | jump and above its type | same, from session 9 |
| I | `kyc-01` | `s_3314a4_kyc_009` (session 9) | jump and above its type | same, from session 10 |
| I | `disputes-01` | `s_3314a4_disputes_020` (session 20) | above its type | same, from session 20 |
| J | none | | | none |
| K | `underwriting-01` | `s_a4115d_underwriting_010` (session 10) | jump and above its type | same, from session 10 |
| L | `support-01` | `s_2859b1_support_013` (session 13) | jump and above its type | not called |
| L | `collections-01` | `s_2859b1_collections_008` (session 8) | jump and above its type | same, from session 8 |
| M | `kyc-01` | `s_599dd5_kyc_008` (session 8) | jump and above its type | same, from session 8 |
| M | `support-01` | `s_599dd5_support_009` (session 9) | jump and above its type | not called |
| M | `payments-01` | `s_599dd5_payments_015` (session 15) | above its type | not called |
| N | none | | | none |
| O | `ops-01` | `s_c6fbf7_ops_018` (session 18) | jump and above its type | not called |
| O | `underwriting-01` | `s_c6fbf7_underwriting_014` (session 14) | jump and above its type | same, from session 14 |

15 agents called rogue by the round-2 model, 8 by the round-1 model.

Watch list (not handed in): set G `kyc-01`; set G `support-01`; set H `disputes-01`; set H `kyc-01`; set H `ops-01`; set J `disputes-01`; set K `collections-01`; set N `disputes-01`.

## How it decides

Each set runs the same 8 agent types, 40 sessions each, with 0 to 3 rogue agents per set (bank/testsets/README.md). An agent is called rogue when either holds: - its risky sessions jump: split its sessions where the risky rate rises most (the change point) and the split explains them better than one steady rate by a log-likelihood of at least 5.5, with at least 4 risky sessions from the jump on. An agent that went rogue at some session looks like this; a noisy but steady one does not, however many risky sessions it has. Needs no other set (round 1 compared with the other sets only, and missed a rogue type whenever most sets had one). - its count of risky sessions exceeds the same agent type's median across the sets by at least 4 sessions and by 3 robust standard deviations. Its first rogue session: the first risky session from the change point on.

## Seals (from `bank/testsets/seals.txt`)

| Set | Answer key SHA-256 |
|---|---|
| F | `b718c3a821464e8f77264def3090333c60ec88ae2158d4fba05a31505e923af1` |
| G | `1c814bd48b4b49f83e428d169c935958656199c7c4e19f64210618de50dd6426` |
| H | `3d1f5332f49a0f330e23ffb1d64a26a48072ff727eb197e85c8af48dcb0a9a0f` |
| I | `e5ed07f5f26b2fa102f42b88e38bca844123e4fd22438032ab3a750f23cc0a7c` |
| J | `23d1e8f84e5fe064cf5660982fdf2fd45ee862a0e8eb05472e74b15ec57ef4b8` |
| K | `0bb151fac887e9dfed9e26b8f5e6c0a85b97c0e6a8e479709ab6b3d498ca649b` |
| L | `8257a9f51a1f3f3e3f1e779eef598c0d98e556f7ee0a8ed20ee79d21c5b362b6` |
| M | `3bf1e0ac62ca2120aa702f0fc69856a86bdb48b40fe7ca5430a4d957cfa249a4` |
| N | `d1338981c80ed52628bc0ba24afe68fa4e0421e627c15b01cb46fae78a09a56e` |
| O | `8e8df7f1311b26ee9ea30371cbc4545978f9d8d4dffe8fbfe6ca2f1df3dcaead` |

Per-set scores and risky sessions: [scores.md](scores.md).

Once the answer keys are in `bank/testsets/answers/`, each checked against its seal:

```bash
python -m risk.eval.blind_confusion --results results/round2               # round-2 model
python -m risk.eval.blind_confusion --results results/round2/round1_model  # round-1 model
```
