# MySQL integration tests

These tests run the MySQL adapter against a real server. They are skipped
unless `THREVO_ACTIONS_TEST_MYSQL_DSN` points at one with root rights, for
example `mysql://root:root@127.0.0.1:3306/mysql`; each test creates and drops
its own database. CI runs them on MySQL 8.0 and 8.4 for Python 3.11–3.13.

- `test_store.py`: the shared store contract, pool and security profile,
  compare-and-set, races and storage limits.
- `test_privileges.py`: the runtime and retention credentials have separate
  database capabilities.
- `test_migrations.py`: migrations are idempotent and checksum-protected, and
  every kind of schema drift fails `inspect` and `migrate`.
- `conftest.py` and `support.py`: database creation and shared fixtures.

Pass the effect identity, or any other binary value, as hex through
`UNHEX(%s)`. aiomysql 0.3.2 cannot escape `bytes` parameters on PyMySQL 1.2.3
or later (see `src/threvo_actions/stores/README.md`).
