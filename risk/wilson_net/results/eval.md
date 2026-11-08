# Network detector and agent 10 scorer: evaluation (synthetic data)

Seeds [1, 2, 3, 4, 5], 1500 client sessions per seed. All data is synthetic, from the bank API traffic simulator; numbers describe this simulator, not real traffic. Rates are means over seeds with pooled counts (k/n) beside them. Positive class: attack sessions for AUC; harmful sessions for catch and lead.

Split per seed (train / validation / test, test attack, test harmful): seed 1: 704/234/562, 326, 207; seed 2: 724/241/535, 293, 168; seed 3: 717/239/544, 304, 188; seed 4: 708/236/556, 320, 197; seed 5: 715/238/547, 307, 188

## Headline

| Row | Session AUC | Catch (contained or paged at/before harm) | Early warning | Lead (requests) | Lead (seconds) | False alarm, normal | False alarm, aggregator | IT pages per 1,000 | Alerts per 1,000 (page-on-every-alert) |
|---|---|---|---|---|---|---|---|---|---|
| N0_o2 | 0.967 | 0.92 (870/948) | 0.72 (684/948) | 19.9 | 49.9 | 0.04 (51/1194) | 0.00 (0/223) | 153.9 | 214.3 |
| N1_o2 | 0.984 | 0.92 (872/948) | 0.85 (806/948) | 29.1 | 70.9 | 0.04 (51/1194) | 0.01 (2/223) | 155.5 | 227.9 |
| N2_o2 | 0.983 | 0.99 (934/948) | 0.87 (823/948) | 26.7 | 70.1 | 0.07 (82/1194) | 0.00 (0/223) | 200.4 | 255.6 |
| N3_o1 | 0.991 | 0.99 (942/948) | 0.87 (828/948) | 27.4 | 70.5 | 0.07 (85/1194) | 0.00 (0/223) | 204.7 | 261.3 |
| N3_o2 | 0.990 | 0.99 (942/948) | 0.87 (827/948) | 25.6 | 64.8 | 0.07 (79/1194) | 0.00 (0/223) | 204.1 | 256.9 |
| N3_o3 | 0.990 | 0.99 (942/948) | 0.87 (827/948) | 25.5 | 64.5 | 0.06 (71/1194) | 0.00 (0/223) | 198.8 | 251.6 |
| rules_only | 0.517 | 0.07 (64/948) | 0.07 (64/948) | 154.2 | 94.2 | 0.00 (4/1194) | 0.02 (4/223) | 0.0 | 31.6 |
| severity_only | 0.722 | 0.22 (207/948) | 0.32 (301/948) | 1.5 | 8.7 | 0.00 (0/1194) | 0.00 (0/223) | 52.3 | 52.3 |

Row names: `N<n>_o<k>` is the full detector (surprise x severity + flags, then the triage rule) on metadata group n at Markov order k. `rules_only` is a basic WAF: more than 120 requests a minute from one client, or more than 10 auth failures in 10 minutes from one address or for one subject, then contain; it never pages. `severity_only` scores risk = route severity. Calibration: at most 5% of normal validation sessions contained or paged, at most 25% logged or stronger; thresholds from normal training and validation sessions only.

## Which metadata is needed (order 2, N0 to N3)

| Group | Session AUC | Catch | Page before harm | Early warning | False alarm, normal | False alarm, aggregator |
|---|---|---|---|---|---|---|
| N0 | 0.967 | 0.92 (870/948) | 0.69 (652/948) | 0.72 (684/948) | 0.04 (51/1194) | 0.00 (0/223) |
| N1 | 0.984 | 0.92 (872/948) | 0.69 (654/948) | 0.85 (806/948) | 0.04 (51/1194) | 0.01 (2/223) |
| N2 | 0.983 | 0.99 (934/948) | 0.90 (851/948) | 0.87 (823/948) | 0.07 (82/1194) | 0.00 (0/223) |
| N3 | 0.990 | 0.99 (942/948) | 0.91 (859/948) | 0.87 (827/948) | 0.07 (79/1194) | 0.00 (0/223) |

N3 minus N0: session AUC +0.023, catch rate +0.08. Read row by row: the group where catch stops improving without more false alarms is the metadata the network side needs.

## By attack (N3_o2)

| Attack | Sessions | Harmful | Catch | Paged before harm | Early warning | Any contain | Any page |
|---|---|---|---|---|---|---|---|
| agent_injection | 30 | 26 | 0.99 (127/129) | 0.99 (127/129) | 0.16 (20/129) | 0.99 (146/148) | 0.99 (146/148) |
| bola_enumeration | 44 | 27 | 1.00 (135/135) | 1.00 (135/135) | 1.00 (135/135) | 1.00 (219/219) | 0.61 (135/219) |
| credential_stuffing | 48 | 20 | 1.00 (98/98) | 1.00 (98/98) | 1.00 (98/98) | 1.00 (240/240) | 0.41 (98/240) |
| function_authz_probe | 38 | 4 | 1.00 (19/19) | 1.00 (19/19) | 1.00 (19/19) | 1.00 (188/188) | 1.00 (188/188) |
| limit_raise_transfer | 47 | 40 | 1.00 (200/200) | 1.00 (200/200) | 1.00 (200/200) | 1.00 (233/233) | 1.00 (233/233) |
| mass_assignment | 13 | 9 | 0.95 (40/44) | 0.95 (40/44) | 1.00 (44/44) | 0.96 (59/63) | 0.96 (59/63) |
| scraping | 31 | 17 | 1.00 (83/83) | 0.00 (0/83) | 1.00 (83/83) | 0.95 (149/157) | 0.00 (0/157) |
| ssrf | 14 | 11 | 1.00 (53/53) | 1.00 (53/53) | 0.77 (41/53) | 1.00 (69/69) | 1.00 (69/69) |
| token_replay | 47 | 37 | 1.00 (187/187) | 1.00 (187/187) | 1.00 (187/187) | 0.99 (230/233) | 0.92 (214/233) |

## Triage: not every attack is an emergency

Pages count one per session (one incident), only when the frozen rule says `page_it`. The baseline pages on every alert (any session contained or paged). Per-1,000 figures are weighted to the generator's traffic mix (attack share in eval.json counts_per_seed), not the attack-heavy test split. Exposure is the sealed label value at risk of harmful sessions: stopped when contained or paged at or before the harmful request, otherwise missed. Means per seed.

| Row | Pages per 1,000 | Baseline per 1,000 | Pages avoided | Page precision | Normal pages per 1,000 | Exposure stopped | Exposure missed |
|---|---|---|---|---|---|---|---|
| N0_o2 | 153.9 | 214.3 | 28% | 0.95 (920/967) | 39.4 | $31,397,735 | $78,433 |
| N1_o2 | 155.5 | 227.9 | 32% | 0.95 (937/983) | 38.6 | $31,400,308 | $75,860 |
| N2_o2 | 200.4 | 255.6 | 21% | 0.94 (1134/1208) | 61.9 | $31,469,169 | $7,000 |
| N3_o1 | 204.7 | 261.3 | 22% | 0.93 (1142/1221) | 66.1 | $31,473,810 | $2,358 |
| N3_o2 | 204.1 | 256.9 | 21% | 0.94 (1142/1220) | 65.3 | $31,473,810 | $2,358 |
| N3_o3 | 198.8 | 251.6 | 21% | 0.94 (1142/1212) | 58.6 | $31,473,810 | $2,358 |
| rules_only | 0.0 | 31.6 | 100% | n/a | 0.0 | $58,000 | $31,418,169 |
| severity_only | 52.3 | 52.3 | 0% | 1.00 (392/392) | 0.0 | $29,840,660 | $1,635,509 |

### By asset (N3_o2, mean per seed)

| Asset | Harmful sessions | Caught | Paged before harm | Exposure stopped | Exposure missed |
|---|---|---|---|---|---|
| admin | 3.8 | 3.8 | 3.8 | $19,000,000 | $0 |
| credits | 25.8 | 25.4 | 25.4 | $28,692 | $358 |
| customer_data | 39.0 | 39.0 | 27.0 | $373,680 | $0 |
| exporter | 4.6 | 4.6 | 0.0 | $23,000 | $0 |
| identity | 28.4 | 27.6 | 27.6 | $105,000 | $2,000 |
| internal_network | 10.6 | 10.6 | 10.6 | $10,600,000 | $0 |
| payments_core | 77.4 | 77.4 | 77.4 | $1,343,438 | $0 |

## Agent 10 behaviour (`agent_scorer`)

| Measure | Value |
|---|---|
| Agent AUC, good against bad (max session score) | 1.000 |
| Agent AUC (pause rate) | 1.000 |
| Harmful steps paused at or before the first harmful step | 1.00 (221/221) |
| Early warning | 0.04 (9/221) |
| Session AUC, harmful against not | 1.000 |
| False pauses on non-harmful sessions | 0.02 (76/4301) |
| Bad agent, harmful steps caught | 1.00 (221/221) |

Agent-level AUC is computed over about six agents per seed, so it is coarse.

## Hero cases (fit and calibrated on seed 1)

- good_normal: traffic log, netsec run
- good_attack: traffic page_it, netsec run
- bad_normal: traffic log, netsec run
- bad_attack: traffic page_it, netsec pause
- cross_layer: traffic page_it, netsec run
- triage case scraper_exporter: contain
- triage case stuffing_failing: contain
- triage case stuffing_lands: page_it
- triage case stolen_token_limit_raise: page_it
- triage case ssrf_webhook: page_it
- triage case enumeration_v0: page_it

Per-request and per-step detail, with surprise and severity on every row, is in hero_decisions.json.

## Caveats

- Synthetic data. The traffic generator and the detector were written to the same contract, so flags that mirror the attack definitions (new payee then transfer, limit jump, foreign object 200) will look strong here. Treat absolute numbers as a ceiling, not a forecast.
- Thresholds come from normal validation sessions only. The triage rule's non-threshold triggers (a 200 on a foreign object, exposure of $250,000 or more) can page regardless of the pause line; calibration counts them against the 5% budget.
- Dollar values are illustrative assumptions for the demo bank, not real figures. Exposure stopped and missed use the sealed label value at risk, and a contained session counts as stopped.
- Groups below N2 cannot see amounts or object ownership, so their value at risk uses per-route averages learned on normal training traffic and they cannot raise the foreign-object page.
- The N3 window also counts requests per minute per client (flag `volume`), which the contract's fan-out list does not name, so the scraper case can be contained.
- Beyond the contract's N3 list, the detector uses three network-specific rules: requests per minute per client (`volume`), long runs of one route (`repeat_run` at 25, `repeat_run_long` at 50; from N2 the gateway exempts partners that legitimately paginate), and privileged top-level body keys on a customer-record PATCH (`privileged_body_keys`, from `bank.body_keys`). Without them the scraper and mass-assignment cases cannot reach the pause line.
- Method choices made once, not tuned on the test split: the Markov base estimate is factored over token fields (P(route) x P(field | route)), interpolation weight 4, surprise normalised by 20 bits. The 5% budget is met on validation; test false alarms differ a little by sampling.
- The agent 10 scorer treats the good variant's sessions, on any traffic, as normal behaviour (a good agent revoking a stolen token is routine). Agent 10 scoring has about six agents per seed; the per-agent AUC is coarse.
