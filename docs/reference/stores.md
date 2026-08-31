# Stores

## Store contracts

::: threvo_actions.stores.base
    options:
      members: true
      show_source: false

## In-memory store

::: threvo_actions.stores.memory
    options:
      members:
        - MemoryActionStore
      show_source: false

The durable adapters are documented in [PostgreSQL and migrations](postgres.md)
and [SQLite and migrations](sqlite.md). Use [Build a custom action
store](../integrations/custom-stores.md) for another database.
