# MySQL and migrations API

## Stores

::: threvo_actions.stores.mysql
    options:
      members:
        - MySQLActionStore
        - MySQLRetentionStore
        - MySQLConnectionSource
        - MySQLAdapterLimitError
        - MySQLStoredDataCorruptionError
      show_source: false

## Migration runner

::: threvo_actions.mysql_migrations
    options:
      members:
        - MySQLMigrationStatus
        - MySQLMigrationStateError
        - MySQLConnectionSource
        - check_mysql_readiness
        - inspect_mysql
        - migrate_mysql
        - mysql_migration_compatibility
        - render_mysql_grants
      show_source: false

See [Migration compatibility](migration-compatibility.md) before automating an
upgrade. Contract migrations on an existing schema require an explicit writer
quiescence acknowledgement.

See [Database readiness](readiness.md) for the read-only startup gate.
