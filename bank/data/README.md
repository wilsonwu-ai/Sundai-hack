# bank/data

Public datasets, used here as synthetic customer requests and synthetic transactions. No real customers. Fetched 2026-11-08 via datasets-server.huggingface.co. Rows containing an email address or phone-number pattern were dropped.

| File | Dataset | License (card) | Rows | Fields |
|---|---|---|---|---|
| hf/banking77.jsonl | PolyAI/banking77 (https://huggingface.co/datasets/PolyAI/banking77), train split | cc-by-4.0 | 3188 (all 77 intents) | text, intent |
| hf/bitext_banking.jsonl | bitext/Bitext-retail-banking-llm-chatbot-training-dataset (https://huggingface.co/datasets/bitext/Bitext-retail-banking-llm-chatbot-training-dataset), `instruction` as text | cdla-sharing-1.0 | 1000 | text, intent, category |
| hf/prompt_injections.jsonl | deepset/prompt-injections (https://huggingface.co/datasets/deepset/prompt-injections), label 1 only, English by word-list heuristic (train+test) | apache-2.0 | 176 | text |
| hf/transactions.jsonl | misatadata/us-bank-transactions (https://huggingface.co/datasets/misatadata/us-bank-transactions), config `transactions`, 6 sampled offsets; amount is abs value | cc-by-4.0 | 595 | merchant, amount_usd, mcc (often null) |

`intent_map.json` maps each of the 77 BANKING77 intents to routine, charge_problem, card_security or account_change (hand-assigned for this project).

Attribution: BANKING77 by Casanueva et al. 2020 (PolyAI, CC BY 4.0); Bitext retail banking dataset (CDLA-Sharing-1.0); deepset prompt-injections (Apache-2.0); misatadata us-bank-transactions (CC BY 4.0).
