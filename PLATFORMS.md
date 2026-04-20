# Platform Support Matrix

Documents which components are Windows-only, macOS-primary, cross-platform, or untested.
Last updated: 2026-04-19 (post macOS migration, v0.29.30).

---

## Runtime / Process Management

| Component | Windows | macOS | Linux |
|-----------|---------|-------|-------|
| Bot runtime (`main.py`) | deprecated (was NSSM service) | primary | untested |
| Instance guard (`_RuntimeInstanceGuard`) | ✅ msvcrt locking | ✅ fcntl flock | ✅ fcntl flock |
| Log rotation (`utils/logger.py`) | ✅ copy+truncate fallback | ✅ copy+truncate | ✅ copy+truncate |
| WAL checkpoint (daily, `main.py`) | ✅ | ✅ | ✅ |

## Automation / Scheduling

| Component | Windows | macOS | Linux |
|-----------|---------|-------|-------|
| Daily review scheduling | `scripts/setup_daily_task.ps1` | `scripts/setup_launchd.sh` | cron (manual — undocumented) |
| Daily review launcher | `scripts/daily_review.ps1` | `scripts/daily_review.py` direct | `scripts/daily_review.py` direct |

## Scripts

| Script | Windows | macOS | Linux |
|--------|---------|-------|-------|
| `scripts/setup_daily_task.ps1` | ✅ Windows Scheduled Task setup | ❌ Windows only | ❌ Windows only |
| `scripts/daily_review.ps1` | ✅ PowerShell launcher | ❌ Windows only | ❌ Windows only |
| `scripts/setup_launchd.sh` | ❌ macOS only | ✅ launchd agent setup | ❌ macOS only |
| `scripts/daily_review.py` | ✅ | ✅ | ✅ |

## Data / Persistence

| Component | Windows | macOS | Linux |
|-----------|---------|-------|-------|
| SQLite (paper trades) | ✅ | ✅ WAL mode | ✅ WAL mode |
| Trade log (JSONL + archive) | ✅ | ✅ | ✅ |
| Evidence store (SQLite) | ✅ | ✅ WAL mode | ✅ WAL mode |

## Notes

- **Windows runtime is deprecated.** The NSSM service setup (`setup_daily_task.ps1`) is preserved for reference but the primary runtime is now macOS.
- **Linux is untested** but no known blockers exist for the core runtime path. Scheduling would require a manual crontab entry.
- **macOS launchd agent** is installed by `scripts/setup_launchd.sh` to `~/Library/LaunchAgents/com.kalshibot.dailyreview.plist`.
- **Unified technical-debt tracking** lives in `docs/profit_path_debt_log.md`; the former macOS migration debt scope has been folded into that single profit-path tracker.
