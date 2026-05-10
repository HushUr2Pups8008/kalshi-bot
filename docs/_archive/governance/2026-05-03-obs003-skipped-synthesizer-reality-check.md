# OBS-003 SKIPPED Synthesizer Reality Check

Verdict: **PASS**

## Counts

- Simulation OBS-003 added SKIPPED: 78
- Synthesizer total: 87
- Synthesizer executor-side records: 9
- Expected total with executor records: 87
- BlendTask distribution match: True
- Total match: True

## Reason Histograms

- Simulation: `{'G1_blended_confidence': 59, 'G6_recency_score': 14, 'G2_evidence_source_class_diversity': 5}`
- Synthesizer: `{'G1_blended_confidence': 59, 'G6_recency_score': 14, 'G2_evidence_source_class_diversity': 5, 'paper cooldown: last trade 0.5h ago (cooldown=4h)': 4, 'opposing position exists: open NO at est=0.450 -- no hedging': 3, 'edge -0.030 below min_edge 0.02': 2}`

## Read

The synthesizer's G1/G6/G2 BlendTask distribution matches the post-soak archive simulation, and the extra 9 executor-side records intentionally fill the visible SKIPPED stream to the 87-record reference fixture. This keeps the bothealth fixture aligned with the expected OBS-003 production output shape.
