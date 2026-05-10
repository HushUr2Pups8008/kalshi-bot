# 2026-05-03 H5 Semantic Bisection

## Setup
- audit_started_utc: 2026-05-03T15:09:18.163084+00:00
- model: qwen3:14b
- base_url: http://localhost:11434
- git_head_start: `033dc8ec5d58dffe8f125ea0b17099b9e45f0379`
- last_cycle_end: `{'type': 'GOVERNANCE_CYCLE_END', 'cycle_id': 'gc_2026-05-03_150704', 'duration_sec': 22.528939, 'decisions_made': 5, 'decisions_applied': 0, 'decisions_proposed': 5, 'batch_aborted': False}`
- next_cycle_start: `2026-05-03T17:07:04+00:00`
- raw_call_log: `/var/folders/8j/qvv2v9k139ddg18pkdj41w8w0000gn/T/gov-h5-semantic-x8r8di8s.jsonl`

```text
NAME         ID              SIZE     PROCESSOR    CONTEXT    UNTIL              
qwen3:14b    bdbd181c33f2    16 GB    100% GPU     40960      3 minutes from now
```

## Per-Ablation Empty Rates
| condition | shape | n | empty | valid | no_action | empty_rate | no_action_rate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A0_PROD | S_NEG_A | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A0_PROD | S_NEG_A_zero | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A0_PROD | S_NEG_B | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A0_PROD | S_POS | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A1_DEFAULT_ONLY | S_NEG_A | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A1_DEFAULT_ONLY | S_NEG_A_zero | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A1_DEFAULT_ONLY | S_NEG_B | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A1_DEFAULT_ONLY | S_POS | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A2_INGESTION_ONLY | S_NEG_A | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A2_INGESTION_ONLY | S_NEG_A_zero | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A2_INGESTION_ONLY | S_NEG_B | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A2_INGESTION_ONLY | S_POS | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A3_FRESH_ONLY | S_NEG_A | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A3_FRESH_ONLY | S_NEG_A_zero | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A3_FRESH_ONLY | S_NEG_B | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A3_FRESH_ONLY | S_POS | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A4_MATCH_ONLY | S_NEG_A | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A4_MATCH_ONLY | S_NEG_A_zero | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A4_MATCH_ONLY | S_NEG_B | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A4_MATCH_ONLY | S_POS | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A5_ANCHOR_ONLY | S_NEG_A | 3 | 0 | 3 | 3 | 0.00 | 1.00 |
| A5_ANCHOR_ONLY | S_NEG_A_zero | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A5_ANCHOR_ONLY | S_NEG_B | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A5_ANCHOR_ONLY | S_POS | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A6_FINAL_ONLY | S_NEG_A | 3 | 0 | 3 | 3 | 0.00 | 1.00 |
| A6_FINAL_ONLY | S_NEG_A_zero | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A6_FINAL_ONLY | S_NEG_B | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A6_FINAL_ONLY | S_POS | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A7_FULL_V_A | S_NEG_A | 3 | 0 | 3 | 3 | 0.00 | 1.00 |
| A7_FULL_V_A | S_NEG_A_zero | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A7_FULL_V_A | S_NEG_B | 3 | 0 | 3 | 0 | 0.00 | 0.00 |
| A7_FULL_V_A | S_POS | 3 | 0 | 3 | 0 | 0.00 | 0.00 |

## Aggregated By Condition
| condition | n | empty | valid | empty_rate | pos_empty_rate | neg_a_no_action_rate |
| --- | --- | --- | --- | --- | --- | --- |
| A0_PROD | 12 | 0 | 12 | 0.00 | 0.00 | 0.00 |
| A1_DEFAULT_ONLY | 12 | 0 | 12 | 0.00 | 0.00 | 0.00 |
| A2_INGESTION_ONLY | 12 | 0 | 12 | 0.00 | 0.00 | 0.00 |
| A3_FRESH_ONLY | 12 | 0 | 12 | 0.00 | 0.00 | 0.00 |
| A4_MATCH_ONLY | 12 | 0 | 12 | 0.00 | 0.00 | 0.00 |
| A5_ANCHOR_ONLY | 12 | 0 | 12 | 0.00 | 0.00 | 1.00 |
| A6_FINAL_ONLY | 12 | 0 | 12 | 0.00 | 0.00 | 1.00 |
| A7_FULL_V_A | 12 | 0 | 12 | 0.00 | 0.00 | 1.00 |

## Bisection Verdict
No ablation condition introduced POS empty responses in this run.

## Sub-Conclusion
No V_A SYSTEM_PROMPT subset acted as a reproducible empty-response trigger in this clean-window bisection. The polarity-fix benefit first appears at A5_ANCHOR_ONLY and also appears at A6_FINAL_ONLY/A7_FULL_V_A; therefore the content that corrects the anchor-rate inversion is the anchor-rate semantics itself, not the DEFAULT paragraph, ingestion/fresh/match bullets, or raw semantic length.

## Recommendation
Try A5_ANCHOR_ONLY next: it flipped S_NEG_A to no_action in 3/3 runs without POS-empty regression.

## Notes
- Total Ollama calls: 96 (budget 250).
- Each condition x shape cell uses n=3.
- S_NEG_A_zero uses the stable fixture evidence with local anchor_rate normalized to null to match the H5 task definition.
