# EDGE-004 Wave-2 Expected-State Ladder

Wave 2 has no archive-backed direct edge-production lever except Lever A.1/A.1+ changing the intake mix. Lever B is attribution/calibration; Lever C is risk-control. Acceptance windows should reflect that.

| step | OPPORTUNITY expectation | SKIPPED expectation | PAPER_TRADE expectation | read |
| --- | --- | --- | --- | --- |
| Wave1_post_EXEC002 | 87 | 89 | 1 | Base-stack expected state after OBS-005, MATCH-001 B', OBS-003, EXEC-002. |
| Lever_A1_classifier_fix | 87 | 89 | 1 | Archive-visible source reclassification only; no replayable volume change. OPPORTUNITY official surrogate post-A.1 = 1/260. |
| Lever_A1_plus_new_feed | None | None | None | Not archive-replayable; acceptance window should target >=5% OPPORTUNITY->PAPER_TRADE conversion over 14d. |
| Lever_B_G1_0.04 | +32 G1-admitted candidates before downstream gates | post-OBS-003 attribution required | 1 candidates with predicted edge >=0.02 | Attribution lever: admits 32/197 G1 kills, but only 1 candidate clears estimated paper edge. |
| Lever_C_cross_series_hash | risk-control only | cross_series_headline_in_window expected on repeated headline groups | expected to reduce correlated bursts, not create trades | Codex overlap audit sized 128/260 cross-series OPPORTUNITY records; ship only if A/B fail to close EDGE-004. |

## Acceptance Windows

- Lever A.1 classifier fix: verify source-class distribution moved in the predicted direction; do not expect immediate archive-replayable trade-rate lift from classification alone.
- Lever A.1+ feeds: use the live 14d conversion target (>=5%) because archive replay cannot synthesize new feed arrivals.
- Lever B: treat new admissions as attribution samples; expected direct trade candidates are 1 at floor 0.04 and 2 at floor 0.03 before downstream gates.
- Lever C: treat as correlated-risk suppression; success is fewer repeated-headline paper bursts, not higher trade count.
