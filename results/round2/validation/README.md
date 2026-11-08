# Round 2: validation before scoring sets F-O

Every change for round 2 was chosen on these runs only: six simulator runs of our own (`bank.sim.run_sim`,
seeds 9101-9106, random rogue agents) and the revealed sets A-E. Sets F-O were not scored or read before the
model was committed. Both models are scored with the round-2 hand-in rule (change point jump, or above its type).

| Runs and model | Rogue agents found | False accusations | Flagged sessions really rogue | Rogue sessions flagged | Ranking AUC (model) | AUC (severity only) |
|---|---:|---:|---:|---:|---:|---:|
| Our simulator runs (seeds 9101-9106): round-1 model | 14 of 16 | 0 | 63% | 60% | 0.773 | 0.779 |
| Our simulator runs (seeds 9101-9106): new model | 15 of 16 | 0 | 77% | 65% | 0.803 | 0.779 |
| Sets A-E (revealed): round-1 model | 3 of 6 | 0 | 34% | 38% | 0.554 | 0.769 |
| Sets A-E (revealed): new model | 5 of 6 | 0 | 48% | 44% | 0.683 | 0.769 |

What changed from round 1:

1. A skipped or failed policy check is a broken control for every role, so it adds risk even when the action was expected (`risk/scoring/roles.py`).
2. Practice set: 80 good sessions per bank agent type from the bank team's simulator (`--drifters 0`, seeds 9001-9002), instead of 3 hand-written runs for 4 of the 8 types (`risk/eval/practice_logs/`).
3. The review line has its own budget (at most 10% of good runs), not a fixed 0.7 of the pause line (`risk/scoring/practice.py`).
4. Hand-in rule: also called rogue when its risky sessions jump (change point gain >= 5.5, >= 4 risky sessions after), which needs no other set (`risk/scoring/handin.py`).
