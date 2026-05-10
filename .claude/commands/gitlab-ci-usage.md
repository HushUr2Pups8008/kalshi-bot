---
description: Show free-tier GitLab CI minute usage for current namespace + month.
argument-hint: [--namespace NAME] [--month YYYY-MM] [--limit N] [--json]
---

Run the GitLab CI/CD compute-minute usage helper. Reports used / remaining
minutes for the current month against the namespace's plan limit (free-tier
default 400 minutes). Flags pass through to the underlying script:

- `--namespace NAME` — top-level namespace (default: parsed from `origin` remote)
- `--month YYYY-MM` — usage month (default: current month, UTC)
- `--limit N` — override monthly compute-minute limit
- `--json` — print machine-readable JSON instead of human summary

Requires `glab` (GitLab CLI) installed and authenticated.

```bash
python3 scripts/gitlab_ci_usage.py "$@"
```
