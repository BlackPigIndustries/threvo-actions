# Action stores

`base.py` defines the `ActionStore` and `RetentionStore` contracts, the stored
proposal model and the create/update validators every adapter applies.
Adapters:

- `memory.py`: in-process store for tests and examples.
- `sqlite.py`: single-file store.
- `postgres.py`: asyncpg adapter (the `postgres` extra).
- `mysql.py`: aiomysql adapter (the `mysql` extra), with a separate retention
  credential for erasure.

The MySQL adapter sends no `bytes` query parameters. aiomysql 0.3.2 escapes
`bytes` with a PyMySQL function that PyMySQL 1.2.3 replaced with a string, so
any `bytes` parameter fails with `'str' object is not callable`. The SHA-256
effect identity is therefore sent as hex and decoded by `UNHEX(%s)` in SQL
(27/10/2026).
