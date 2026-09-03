# Database readiness API

Readiness checks are explicit, read-only startup gates for the official
PostgreSQL and MySQL adapters. They verify that migration history is current
and that the connected credential matches either the runtime or retention
lane. They never migrate, grant privileges, or write action data.

`ActionApplication.inspect()` is not readiness. It reports allowlisted static
semantics without opening a dependency scope. Adapter-specific migration
inspection APIs and CLI `inspect` commands may report applied and pending
migrations, but they do not validate the application's credential boundary or
act as its startup gate. The readiness checks uniquely combine current
migration posture with runtime-or-retention credential posture. Static
inspection, migration inspection, and readiness can never authorize an action.

Call the adapter-specific function after creating the application's pool and
before accepting work. Stop startup when `ready` is false and surface `issues`
to operators. Do not silently continue with a migrator credential.

MySQL readiness intentionally requires the direct grants emitted by
`render_mysql_grants`. Assigned roles or additional grants fail the exact
posture check, even if they happen to produce similar effective access. This
keeps the boot-time result deterministic; qualify a custom role-based profile
separately instead of weakening the official one.

::: threvo_actions.readiness
    options:
      members:
        - DatabaseAdapter
        - DatabaseAccessLane
        - DatabaseReadiness
      show_source: false
