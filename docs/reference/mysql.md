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
        - inspect_mysql
        - migrate_mysql
      show_source: false
