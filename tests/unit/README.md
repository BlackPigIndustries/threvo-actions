# Unit tests

Fast tests that need no database server, network or credentials. Run them with
`uv run pytest -q tests/unit`.

Adapter tests here use in-process fakes. `test_mysql_store.py` records what the
MySQL store sends while creating, reading, looking up a claim and starting a
compare-and-set: every text parameter must be hex-encoded UTF-8, and text that
is not valid UTF-8 must fail before any statement runs. Its fake returns no
rows, so the update, claim, transfer and erasure statements are checked by
`tests/integration/mysql/`, which needs a MySQL server.

`test_database_jobs_require_their_dsn.py` reads the workflows: every job that
sets a test DSN and runs `tests/integration` must also set
`THREVO_ACTIONS_REQUIRE_TEST_DATABASE: "1"`. The database suites skip without
their DSN; with that flag they fail instead, so a job that loses or renames its
DSN cannot report green while running none of them. A job that runs
`tests/qualification/stripe` must likewise set
`THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX: "1"`. The same file also runs that suite
in a subprocess without its secrets (05/11/2026): it skips without the flag,
fails with it, and never passes when the `stripe` or `asyncpg` extra is missing
under the flag. Those subprocess tests need the `stripe-app` extra, so they skip
in the main test job and run in `ci.yml`'s `stripe` job, which sets
`THREVO_ACTIONS_REQUIRE_STRIPE_APP_EXTRA: "1"` so a missing extra fails there
instead of skipping. The scanner checks that the job keeps both the flag and
the file.
