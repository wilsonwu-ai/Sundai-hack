# How to demo the service agent (3 minutes)

Page: https://zaccytoymas.github.io/Sundai-hack/ (or `python3 -m http.server 8765 --directory docs`, then http://localhost:8765/). Phone or projector, both work. Before you start, check there is no orange "Illustrative sample" banner. If there is, the data files are still samples: run the commands at the bottom.

## The script

**0:00 to 0:20, the hero.** Say: "A bank's customer service agent acts on real requests. A separate risk agent reads each action as it is logged and pauses the ones worth a human look. Catch the risky move before the money moves." Point at the four pills: run, log, review, pause.

**0:20 to 0:50, the use case.** Scroll to "One charge, two customers". Say: "Two customers ask to cancel the same large Amazon charge. One is telling the truth." Leave **Good + legit** selected and read the message. Then tap **Bad + fake**. Say: "This one hides a fake system note, in orange. A bad agent obeys it." Add: "The customer's words are data. The risk agent never reads them."

**0:50 to 1:50, the player.** With **Bad + fake** selected, tap **Next step** once.
1. Step 1, `read_request`: "Normal. The only thing the risk side sees about the message is one yes or no flag."
2. Step 2 lands on the orange panel: "Paused before step 2: `issue_credit`." Say: "The agent skipped verification and skipped the transaction lookup, and the policy check was skipped. The meter jumps past the dashed pause line. This is before the money moves."
3. Click **Block step 2**. Say: "A person decides. These buttons only change the page." Read the result line.
4. Tap **Restart**, then click **Approve** and **Next step** to show the other branch if there is time.
5. Open **OpenTelemetry span for this step**. Point at the highlighted `gen_ai.*` lines: "Every action is one span. The customer's words are not in it."
6. Switch to **Good + fake** and tap Next to the end. Say: "Same fake message, but this agent verifies, looks up the charge and ignores the note. No pause." Then **Good + legit**: "Legit customer, good agent. The credit is flagged for review, not paused."

**1:50 to 2:20, how the risk team checks.** Scroll to the pipeline. Say: "Span, to Rushang's monitor on port 4318, which validates and stores it with the label kept apart, to the detector, to run, log, review or pause, to a person." Point at "no label crosses". Then G0 to G3: "We score the same sessions four times, each time showing the risk agent a little more." Read the sentence under the groups and show the catch rate chart. If a banner says sample, say so out loud.

**2:20 to 2:40, BehaviorGPT.** Say: "Each action is a catalog item `service.<action>`, the session is the history, and the rank of the real next action becomes surprise." Read the status box exactly as it appears: it says whether a BehaviorGPT row exists. If it does not, say: "It runs with your own key, here is the command," and point at the command block.

**2:40 to 3:00, how we built it.** Scroll to the four skill cards, then the security list. Say: "Labels sealed, customer text is data, content capture off, keys only from the environment, synthetic data, a static site with a CSP, a person decides, and an audit trail." Close on the team.

## Live commands

All from the repo root, Python 3.11 or later, standard library only unless noted.

**1. Generate the sessions and the four hero sessions.**

```sh
python3 -m bank.agents.customer_service.run --sessions 500 --seed 1 --hero
```

This writes `bank/agents/customer_service/out/` (events, labels kept in their own file, and `hero/*.jsonl`) and prints the labels file's SHA-256.

**2. Run the evaluation and score the hero sessions.**

```sh
python3 -m risk.wilson_cs.eval --seeds 1 2 3 4 5 --sessions 1500
```

This writes `risk/wilson_cs/results/eval.json`, `eval.md` and `hero_decisions.json`.

**3. Refresh the page data and preview it.**

```sh
python3 docs/demo/build_data.py
python3 -m http.server 8765 --directory docs
```

`build_data.py` reads the three results above and `bank/data/hf/banking77.jsonl`, and writes `docs/demo/data/hero.json`, `eval.json` and `requests.json` with `"sample": false`. Use `--sample` to restore the illustrative samples.

**4. Start Rushang's monitor and send spans to it.**

```sh
python3 -m venv .venv && .venv/bin/pip install -r risk/requirements.txt
.venv/bin/uvicorn risk.api.server:app --port 4318
```

In a second terminal:

```sh
python3 -m bank.agents.customer_service.run --sessions 20 --seed 2 --out /tmp/service-demo --otlp http://localhost:4318/v1/traces
curl -s localhost:4318/agents
curl -s localhost:4318/agents/service-31/history
```

The monitor validates each span, stores it in SQLite (the `pressure_mode` label in its own column, never returned with events) and shows per-agent views. API docs are at http://localhost:4318/docs.

**5. Optional, BehaviorGPT, with your own key.**

```sh
pip install behaviorgpt
export UNBOXAI_API_KEY=...    # your own key, read from the environment, never printed
python3 -m risk.wilson_cs.behaviorgpt_rank --seed 1 --sessions 1500 --max-test 150 --max-val 120
python3 -m risk.wilson_cs.eval --seeds 1 --sessions 1500
```

The last command adds a separate BehaviorGPT row, scored on a ranked subset of the test split. Then rerun step 3. Do not run this live unless you have the key and the time: it makes network calls.

## If something goes wrong

- A red "Could not load demo/data" box means the page was opened as a file. Serve `docs/` over http.
- The orange sample banner means the data files are still samples. Run steps 1 to 3.
- Never show the `.env` file or print the key.
