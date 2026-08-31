# SQLite and migrations API

## Stores

::: threvo_actions.stores.sqlite
    options:
      members:
        - SQLiteActionStore
        - SQLiteRetentionStore
        - SQLiteStoredDataCorruptionError
      show_source: false

## Migration runner

::: threvo_actions.sqlite_migrations
    options:
      members:
        - SQLiteMigrationStatus
        - SQLiteMigrationStateError
        - inspect_sqlite
        - migrate_sqlite
        - sqlite_migration_compatibility
      show_source: false
