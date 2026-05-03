# 2026-05-03 Empty-Response Audit

## Setup
- audit_started_utc: 2026-05-03T14:00:26.643047+00:00
- model: qwen3:14b
- base_url: http://localhost:11434
- git_head_start: `051f391d15b6781aca26ee129e2144fc5f51cd13`
- last_cycle_end: `{'type': 'GOVERNANCE_CYCLE_END', 'cycle_id': 'gc_2026-05-03_130644', 'duration_sec': 20.285492, 'decisions_made': 5, 'decisions_applied': 0, 'decisions_proposed': 5, 'batch_aborted': False}`
- next_cycle_start: `2026-05-03T15:06:44+00:00`
- raw_call_log: `/var/folders/8j/qvv2v9k139ddg18pkdj41w8w0000gn/T/gov-empty-response-z59dgsgp.jsonl`

```text
NAME    ID    SIZE    PROCESSOR    CONTEXT    UNTIL
```

## H1 Concurrency
| condition | n | empty | valid | empty_rate |
| --- | --- | --- | --- | --- |
| back_to_back | 9 | 0 | 9 | 0.00 |
| gap_30s | 9 | 0 | 9 | 0.00 |
| parallel_2proc | 6 | 0 | 6 | 0.00 |

Verdict: **not the driver**.

## H2 Evidence Shape
| shape | n | empty | valid | empty_rate |
| --- | --- | --- | --- | --- |
| anchor_no_matches | 3 | 0 | 3 | 0.00 |
| borderline | 3 | 0 | 3 | 0.00 |
| dormant_zero | 3 | 0 | 3 | 0.00 |
| heuristic_anchor | 3 | 0 | 3 | 0.00 |
| neg_high_signal | 3 | 0 | 3 | 0.00 |
| neg_low_anchor | 3 | 0 | 3 | 0.00 |
| pos_zero | 3 | 0 | 3 | 0.00 |
| sparse_anchor_matches | 3 | 0 | 3 | 0.00 |

Verdict: **not the driver**.

## H3 Prompt Length
| prompt | n | empty | valid | empty_rate |
| --- | --- | --- | --- | --- |
| plus_15 | 3 | 0 | 3 | 0.00 |
| plus_30 | 3 | 0 | 3 | 0.00 |
| plus_5 | 3 | 0 | 3 | 0.00 |
| prod | 3 | 0 | 3 | 0.00 |

Verdict: **not the driver**.

## H4 Sequential Accumulation
| condition | n | empty | valid | empty_rate |
| --- | --- | --- | --- | --- |
| sequential_50 | 50 | 0 | 50 | 0.00 |

First 10 empty count: 0; last 10 empty count: 0.

Verdict: **not the driver**.

## Combined Verdict
1. H1 concurrency: not the driver
2. H2 evidence shape: not the driver
3. H3 prompt length: not the driver
4. H4 sequential accumulation: not the driver

No H1-H4 axis reproduced the empty-response failure in this clean-window run.

## Recommendation
Hold daemon state constant first: start every GOV-002 prompt iteration with a PROD sentinel pair (one anchor_rate=None zero-match POS control and one non-null-anchor high-signal control), run in a clean cycle gap, and do not compare prompt variants unless the sentinel pair returns valid JSON.

## Unexpected Discoveries
- The clean-window audit produced 0 empty responses across every sparse POS shape, every non-null-anchor NEG_A/NEG_B shape, every prompt-length variant, and all 50 sequential calls. That points away from stable prompt-content or evidence-shape causality and toward transient daemon/session state not directly represented by H1-H4.

## Notes
- Total Ollama calls: 110 (budget 250).
- Counts are simple n/N rates; most cells intentionally use n=3.
