# Migration compatibility API

Every official adapter publishes immutable metadata next to its packaged SQL.
Use it in deployment tooling to distinguish a safe schema expansion from a
contract change that cannot overlap with the previous runtime.

`compatible_with_previous_runtime=False` means the old application must not
keep writing after that migration begins. `requires_writer_quiescence=True`
means an existing schema upgrade fails until `writers_quiesced=True` is passed
to the adapter migration function, or `--writers-quiesced` is passed to its CLI
command. The flag is an acknowledgement, not a mechanism that stops processes.
Drain runtime and retention writers before setting it.

Fresh schema bootstrap has no older supported runtime to drain, so it does not
require the acknowledgement.

::: threvo_actions.migration_compatibility
    options:
      members:
        - MigrationPhase
        - MigrationCompatibility
        - migrations_requiring_writer_quiescence
      show_source: false
