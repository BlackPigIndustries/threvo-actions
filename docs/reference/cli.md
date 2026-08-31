# Command-line interface

```text
threvo-actions skill path
threvo-actions postgres inspect --dsn-env DATABASE_URL [--schema threvo_actions]
threvo-actions postgres plan --dsn-env DATABASE_URL [--schema threvo_actions]
threvo-actions postgres ready --dsn-env DATABASE_URL [--schema threvo_actions] --lane runtime|retention
threvo-actions postgres migrate --dsn-env DATABASE_URL [--schema threvo_actions] [--writers-quiesced]
threvo-actions postgres grants --schema threvo_actions --runtime-role ROLE --retention-role ROLE
threvo-actions mysql inspect --dsn-env DATABASE_URL
threvo-actions mysql ready --dsn-env DATABASE_URL --lane runtime|retention
threvo-actions mysql migrate --dsn-env DATABASE_URL [--writers-quiesced]
threvo-actions mysql grants --database NAME --runtime-user USER --runtime-host HOST --retention-user USER --retention-host HOST
threvo-actions sqlite inspect --database PATH
threvo-actions sqlite migrate --database PATH
```

`skill path` is read-only. It prints the absolute directory of the coding-agent
skill bundled with the installed distribution or present in the source
checkout.

PostgreSQL commands accept the **name** of an environment variable containing
the DSN. Inspect first, authorize the exact target outside the library, migrate
with a migration-capable role, and inspect again. Do not place a raw DSN on the
command line.

`postgres plan` performs the same read-only inspection, then emits JSON with
the exact schema-rendered SQL, immutable source checksum, compatibility phase,
and quiescence requirement for each pending migration. It does not include or
execute the migration ledger updates and is a review artifact, not a substitute
for `migrate`.

PostgreSQL and MySQL contract migrations refuse an existing schema upgrade
without `--writers-quiesced`. The option records the operator's explicit
acknowledgement; it does not stop workers. Inspect first, drain runtime and
retention writers, pass the option, inspect again, and only then resume them.
Fresh schema bootstrap does not require the option.

The `grants` commands are offline renderers. They need no driver, DSN, or
database connection. They quote deployment-owned names and emit the privilege
set exercised by the native separated-credential tests. They do not create
users or roles, revoke privileges from named application accounts, or apply the
SQL. Use dedicated accounts, review the output, and apply it with the migrator.

`ready` is the application startup check. Run it with the actual runtime or
retention DSN and matching lane. It prints a bounded JSON result and exits `0`
only when migrations are current and the credential has the expected privilege
boundary; an unsafe result exits `3`. It never repairs the schema or grants.

SQLite commands require no optional dependency. `inspect` does not create a
missing database. `migrate` applies the packaged SQLite schema explicitly;
constructing a SQLite store never migrates it automatically.

::: threvo_actions.cli
    options:
      members: true
      show_source: false
