# Action stores

`base.py` defines the `ActionStore` and `RetentionStore` contracts, the stored
proposal model and the create/update validators every adapter applies.
Adapters:

- `memory.py`: in-process store for tests and examples.
- `sqlite.py`: single-file store.
- `postgres.py`: asyncpg adapter (the `postgres` extra).
- `mysql.py`: aiomysql adapter (the `mysql` extra), with a separate retention
  credential for erasure.

The MySQL adapter moves text as hex-encoded UTF-8 in both directions (28/10/2026):

- Parameters: `_text` hex-encodes, and SQL decodes with `_TEXT`, that is
  `CONVERT(UNHEX(%s) USING utf8mb4) COLLATE utf8mb4_bin`. Text that is not
  valid UTF-8 raises `MySQLAdapterLimitError` before it is sent. Integers and
  datetimes stay ordinary parameters.
- Results: text columns are selected as `HEX(...)` and decoded by
  `_stored_text`, so a pool in another charset reads back exactly what was
  stored.
- Lookups: `utf8mb4_bin` ignores trailing spaces, so `_load` and
  `_effect_owner` treat a row whose references differ from the requested ones
  as not found.

It also sends no `bytes` parameters. aiomysql 0.3.2 escapes `bytes` with a
PyMySQL function that PyMySQL 1.2.3 replaced with a string, so any `bytes`
parameter fails with `'str' object is not callable`. The SHA-256 effect
identity is therefore sent as hex and decoded by `UNHEX(%s)` (27/10/2026).
