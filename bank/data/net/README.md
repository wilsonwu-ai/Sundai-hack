# bank/data/net

Payload strings for the bank API payload screen (agent 10, network watch).

## Source

- Dataset: `notesbymuneeb/ai-waf-dataset` ("Synthetic HTTP Requests Dataset for AI WAF Training")
- Link: https://huggingface.co/datasets/notesbymuneeb/ai-waf-dataset
- License: `mit` (the card's YAML header says `license: mit`; attribution to the dataset author, notesbymuneeb, is kept here)
- Fetch date: 2026-11-08
- Rows read: 2,730 of 11,949 (config `default`, split `train`) through datasets-server `/rows`. The first 8,658 rows are benign requests and the rest are malicious, so malicious rows were sampled at 20 evenly spaced 100-row windows plus 2 extra windows, and benign rows at 6 spread windows.
- The dataset is synthetic. Each row is a full HTTP request with a `benign` or `malicious` label and no attack subtype, so subtype labels here come from the filter below, not from the dataset.

## How rows were produced (`http_payloads.jsonl`)

1. From each request, candidate strings were pulled out: the query string, the body, the URL path (when it had traversal-like content), and the Cookie, Referer, User-Agent and `X-*` header values.
2. From rows labelled malicious, a candidate became `xss`, `path_traversal` or `sqli` when it matched a regex for that attack (checked in that order, after URL-decoding twice for matching only; the text kept is decoded once). Candidates matching none of the three are dropped (command injection, SSTI and so on are out of scope).
3. From rows labelled benign, candidates matching any attack regex were dropped. Kept: query strings, form bodies, and single parameter values that contain letters (search terms and form values).
4. Rows containing an email address or a phone-like number are dropped. IPv4 addresses are replaced with `203.0.113.x`. Domain names are replaced with `example.com`.
5. Length 4 to 300 characters, deduplicated on exact text, then capped at 220 rows per kind with a seeded shuffle.

## Rows written

| kind | rows |
|---|---|
| sqli | 220 |
| xss | 135 |
| path_traversal | 172 |
| benign | 220 |
| total | 747 |

## Not included

- `user_agents.jsonl`: no row-level licensed browser, mobile, bot and tool user agent source was found with a stated license that fits. The AI crawler registries (cc-by-4.0 / mit) cover bots only and were not fetched. Agent 10 traffic code should use its built-in samples.
- Other licensed candidates seen and not used: `YangYang-Research/web-attack-detection` and `truongp/web-attack-detection` (mit; labels mix unrelated text with attack strings and carry no attack type), `darkknight25/Web_Application_Payloads_Dataset` (mit; under 1k rows, not fetched), `Kaveny/sql-injection` (apache-2.0; chat transcripts, not payloads). `miomit/xss_generation` is gpl-3.0 and was skipped.
- Datasets whose card states no license were skipped.

Because the source is synthetic, rows are realistic in form but are not captured traffic.
