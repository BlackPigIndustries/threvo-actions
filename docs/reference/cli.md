# Command-line interface

```text
threvo-actions skill path
threvo-actions postgres inspect --dsn-env DATABASE_URL [--schema threvo_actions]
threvo-actions postgres migrate --dsn-env DATABASE_URL [--schema threvo_actions]
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

SQLite commands require no optional dependency. `inspect` does not create a
missing database. `migrate` applies the packaged SQLite schema explicitly;
constructing a SQLite store never migrates it automatically.

::: threvo_actions.cli
    options:
      members: true
      show_source: false
