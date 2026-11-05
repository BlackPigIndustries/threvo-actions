# PostgreSQL integration tests

These tests run the PostgreSQL store against a real server. They are skipped
unless `THREVO_ACTIONS_TEST_POSTGRES_DSN` points at a disposable test database.
CI runs them in the `postgres` job on PostgreSQL 15 and 16.

A skip prints as green. With `THREVO_ACTIONS_REQUIRE_TEST_DATABASE=1` a missing
DSN fails these tests instead; the CI job sets it.

- `test_store_conformance.py` and `test_work_discovery.py`: the shared store
  contract and work discovery.
- `test_codec_and_tenant_isolation.py` and `test_constraints_and_leakage.py`:
  value encoding, tenant isolation and database constraints.
- `test_migrations.py` and `test_privileges.py`: migrations and the separate
  database rights of each credential.
- `conftest.py`: database creation and shared fixtures.
