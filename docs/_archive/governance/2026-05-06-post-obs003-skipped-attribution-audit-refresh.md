# 2026-05-06 post-OBS-003 SKIPPED attribution audit refresh

**Command:**

```bash
.venv/bin/python scripts/simulations/post_obs003_skipped_attribution_audit.py --json
```

## Result

| metric | value |
|---|---:|
| paths scanned | 6 |
| total `SKIPPED` | 9 |
| BlendTask-emitted | 0 |
| executor-emitted | 9 |

## Reasons

| reason | count |
|---|---:|
| `edge +0.0000 below min_edge 0.02` | 8 |
| `paper cooldown: last trade 0.0h ago (cooldown=4h)` | 1 |

## Contract read

No post-OBS-003 BlendTask SKIPPED records exist in the current archive window;
all observed `SKIPPED` rows are executor-emitted. That is expected pre-OBS-003
deploy and does not validate the post-deploy attribution contract yet.
