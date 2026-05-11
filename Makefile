PYTHON ?= .venv/bin/python

.PHONY: test-safe test-safe-detached run-history db-wal-check db-wal-checkpoint gitlab-ci-usage lint lint-fix coverage botcheck trade-summary decision-funnel freshness pipeline-impact governance-monitor governance-review soak-invariant hook-health

test-safe:
	scripts/run_tests.sh

test-safe-detached:
	scripts/run_tests.sh --detach

run-history:
	scripts/show_run_registry.py

botcheck:
	$(PYTHON) scripts/botcheck.py

trade-summary:
	$(PYTHON) -m scripts.trade_log_summary --since $$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d yesterday +%Y-%m-%d)

decision-funnel:
	$(PYTHON) -m scripts.decision_funnel_summary --since $$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d yesterday +%Y-%m-%d)

freshness:
	$(PYTHON) -m scripts.freshness_diagnostics --since $$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d yesterday +%Y-%m-%d)

pipeline-impact:
	$(PYTHON) scripts/pipeline_impact_audit.py --hours 24

governance-monitor:
	$(PYTHON) scripts/governance_monitor.py --since $$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d yesterday +%Y-%m-%d)

governance-review:
	$(PYTHON) scripts/governance_decision_review.py --since $$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d yesterday +%Y-%m-%d)

soak-invariant:
	bash scripts/check_soak_invariant.sh

hook-health:
	bash scripts/precommit_hook_health_audit.sh

db-wal-check:
	scripts/check_sqlite_wal.sh

db-wal-checkpoint:
	scripts/check_sqlite_wal.sh --checkpoint

gitlab-ci-usage:
	python3 scripts/gitlab_ci_usage.py

lint:
	ruff check .

lint-fix:
	ruff check --fix .

coverage:
	pytest --cov --cov-report=term-missing --cov-report=html
