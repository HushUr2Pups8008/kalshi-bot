.PHONY: test-safe test-safe-detached run-history db-wal-check db-wal-checkpoint

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
