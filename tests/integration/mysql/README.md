# MySQL integration tests

These tests run the MySQL adapter against a real server. They are skipped
unless `THREVO_ACTIONS_TEST_MYSQL_DSN` points at one with root rights, for
example `mysql://root:root@127.0.0.1:3306/mysql`; each test creates and drops
its own database. CI runs them on MySQL 8.0 and 8.4 for Python 3.11–3.13.

- `test_store.py`: the shared store contract, pool and security profile,
  compare-and-set, races, storage limits, exact reference matching, text
  their charset cannot represent on `gbk` and `latin1` pools, and the wire
  format of every parameter across the whole lifecycle.
- `test_privileges.py`: the runtime and retention credentials have separate
  database capabilities.
- `test_migrations.py`: migrations are idempotent and checksum-protected, and
  the drift kinds covered (engine, columns, indexes and primary key, foreign
  key, checks, triggers, routines, identifier case, invoker security) fail
  `inspect` and `migrate`.
- `conftest.py` and `support.py`: database creation and shared fixtures.

The store sends text as `_text(value)` through the `_TEXT` SQL fragment and
binary values as hex through `UNHEX(%s)`. `support.sent_text` pairs each
recorded parameter with its placeholder and fails on a string bound any other
way; the unit test and `test_every_text_parameter_reaches_mysql_as_utf8_hex`
both use it. aiomysql 0.3.2 cannot escape `bytes` parameters on PyMySQL 1.2.3
or later (see `src/threvo_actions/stores/README.md`).
