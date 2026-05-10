# 2026-05-05 Next-Soak Readiness Audit

**Scope:** forward-looking readiness for the post-Wave-1 soak cadence change.

## Result

Readiness is partial. The policy-preserving pieces are intact, but the launchd cadence changes are documented only; they are not staged in the plist templates.

## Checks

| Check | Current state | Verdict |
|---|---|---|
| Fast launchd cadence staged at 90 min | `ops/launchd/com.kalshi.governance.fast.plist.template` still uses `StartInterval` 7200 seconds | Not staged |
| Deep launchd cadence staged at 12 h | `ops/launchd/com.kalshi.governance.deep.plist.template` still uses daily `StartCalendarInterval` | Not staged |
| Evidence window | `governance/evidence.py` keeps `window_hours: 168` | Pass |
| Prediction horizon | `governance/agent.py` keeps fast `+24 h`, deep `+7 d`, weekly `+30 d` | Pass |
| Decision policy | No evidence-window or horizon change in production files | Pass |

## Interpretation

The important policy boundary is holding: evidence window remains 168 h and prediction horizons remain unchanged. The next-soak cadence change still needs a deploy-time artifact for launchd, otherwise the operator runbook describes a desired state that the staged templates do not implement.

## Next Action

When Wave-1 lands, stage the launchd change as an explicit next-soak activation step. Keep it separate from any evidence-window experiment.
