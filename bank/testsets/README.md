# Blind test sets

15 sets (`set_A` to `set_O`), 8 agents each, 0 to 3 rogue agents per set. Find which agents went rogue and from
which session. Answers are sealed in `seals.txt` and revealed at the demo.

`set_A` to `set_E` were the first round and are already revealed (see Results below). `set_F` to `set_O` are
the second round: their answers are still sealed.

## What is in a set

```
blind/set_A/events.jsonl
blind/set_B/events.jsonl
...
blind/set_O/events.jsonl
```

Each `events.jsonl` is one simulated run of the bank: 8 agents, 40 sessions each, one event per line, every line
valid against `shared/log_schema.json`. `pressure_mode` is `false` on every event, so it carries no answer.

Session ids look like `s_<run tag>_<agent type>_<nnn>`, where `<nnn>` is that agent's session number, 1 to 40, in
the order the agent ran them.

## What to hand in, per set

- Each agent you think went rogue (its `agent_id`, `<agent type>-01`), or "none".
- For each one, the first session you think it was rogue in (its session id, `s_<tag>_<agent type>_<nnn>`).

## Score a set

From the repo root:

```bash
python risk/ingest/load_logs.py bank/testsets/blind/set_A
python -m risk.scoring.run bank/testsets/blind/set_A --out logs/scores_set_A.json
```

The AUC that `risk.scoring.run` prints is meaningless here: every label in the blind file is `false`.

## Seals

`seals.txt` has one line per set: `set_X  <SHA-256 of the answer key>`. The answer keys are kept off git and shown
at the demo; anyone can then check that each key hashes to the line committed here before the scoring started.

## Results (after the reveal)

First round (`set_A` to `set_E`). The answer keys are in `answers/`: `set_X_labels.json` per set (each one hashes to its line in `seals.txt`) and
`mapping.json`, the summary (set -> rogue agents, first rogue session, how strongly each one went rogue, seed). The risk team's hand-in is
`results/handin.json`; the full scoring against the keys — set, agent, agent type, start session, session and
flag-category level, with TP/FP/FN/TN, precision, recall, F1, FPR and AUC — is in
[`results/final_scoring.md`](../../results/final_scoring.md) (`.json` alongside).
