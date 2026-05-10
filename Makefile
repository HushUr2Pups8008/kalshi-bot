.PHONY: test-safe test-safe-detached run-history db-wal-check db-wal-checkpoint gitlab-ci-usage lint lint-fix coverage

test-safe:
	scripts/run_tests.sh

test-safe-detached:
	scripts/run_tests.sh --detach

run-history:
	scripts/show_run_registry.py

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
