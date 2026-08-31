# PostgreSQL and migrations API

## Stores

::: threvo_actions.stores.postgres
    options:
      members:
        - PostgresActionStore
        - PostgresRetentionStore
        - ConnectionSource
        - StoredDataCorruptionError
      show_source: false

## Migration runner

::: threvo_actions.migrations
    options:
      members:
        - MigrationStatus
        - ConnectionSource
        - InvalidSchemaNameError
        - MigrationStateError
        - inspect_postgres
        - migrate_postgres
        - postgres_migration_compatibility
        - quote_schema_name
      show_source: false

See [Migration compatibility](migration-compatibility.md) before automating an
upgrade. Contract migrations on an existing schema require an explicit writer
quiescence acknowledgement.
