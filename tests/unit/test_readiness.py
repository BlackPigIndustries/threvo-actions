from __future__ import annotations

from threvo_actions.readiness import (
    DatabaseAccessLane,
    DatabaseAdapter,
    DatabaseReadiness,
)


def test_database_readiness_requires_current_schema_and_valid_privileges() -> None:
    ready = DatabaseReadiness(
        adapter=DatabaseAdapter.POSTGRESQL,
        lane=DatabaseAccessLane.RUNTIME,
        applied_versions=(1, 2, 3, 4),
        pending_versions=(),
        schema_current=True,
        privilege_boundary_valid=True,
        issues=(),
    )
    blocked = DatabaseReadiness(
        adapter=DatabaseAdapter.MYSQL,
        lane=DatabaseAccessLane.RETENTION,
        applied_versions=(1,),
        pending_versions=(2,),
        schema_current=False,
        privilege_boundary_valid=False,
        issues=("database migrations are pending",),
    )

    assert ready.ready
    assert not blocked.ready
