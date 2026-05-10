# Adversarial review: latest six Claude commits

Date: 2026-05-05
Reviewed commits: `37063d8`, `ff04b7f`, `dbfb42b`, `72715d9`, `037d76d`, `6ec311f`

## Findings

### F1 - Escalation doc still carries dropped Branch B

Severity: medium

`6ec311f` correctly drops direct VitalLaw RSS as an active branch in the A.1+1.5 spec, TL;DR, and rehearsal checklist after the direct-RSS probe found no public feed. But `docs/governance/post-edge-004-escalation-paths.md:19` still lists:

`Branch B (active VitalLaw direct RSS): probe fails or paywall-locks`

Impact: the escalation entry criteria now disagree with the simplified decision tree. A future operator can think Branch B still needs a Day-14 action before escalation eligibility, even though the current tree says to skip it.

Expected close: update the escalation doc to remove Branch B and make Branch C/open-RSS analogues the active fallback after Branch A observation.

### F2 - A.1+1.5 spec says it covers Branch B after Branch B was dropped

Severity: low

`docs/superpowers/specs/2026-05-04-edge-004-lever-a1plus1-5-legal-analyst-design.md:29` says the spec covers Branch B and Branch C. The same document later says Branch B is empirically infeasible and the decision tree collapses to A/C/D.

Impact: minor doc contradiction in the spec scope statement.

Expected close: change the scope line to say the deployable code path is Branch C; Branch A is observation and Branch B is historical context only.

### F3 - TL;DR keeps legacy option-B table text that now overstates VitalLaw direct restore

Severity: low

The TL;DR preserves the legacy option-A/option-B table for harness naming. That is useful, but the table still says option-B lift is conditional on accessibility and that VitalLaw probe success may restore the load-bearing source. The current decision tree says the direct RSS probe is dropped and the Google News path is already active.

Impact: operator readability. The superseded table can pull attention back to a dead direct-RSS path.

Expected close: mark the legacy table as naming-only and remove the "VitalLaw probe succeeds" wording.

## Positive checks

- Lever B harness is narrow and useful: `G1_CONFIDENCE_THRESHOLD` pins the post-deploy lower bound while preserving the 2x failsafe invariant.
- Lever C harness correctly frames the lever as risk-control and pins both config and emitted reason string.
- Aggregator-path forensics concrete identification is a strong correction: PAPER_TRADE-producing VitalLaw `SIGNAL` rows point to Google News RSS.
- Focused preload tests: `4 passed, 4 xfailed` across Lever B/C.
