"""Framework-neutral database startup readiness results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DatabaseAdapter(StrEnum):
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQLITE = "sqlite"


class DatabaseAccessLane(StrEnum):
    RUNTIME = "runtime"
    RETENTION = "retention"


@dataclass(frozen=True)
class DatabaseReadiness:
    """Read-only schema and privilege readiness for one application lane."""

    adapter: DatabaseAdapter
    lane: DatabaseAccessLane
    applied_versions: tuple[int, ...]
    pending_versions: tuple[int, ...]
    schema_current: bool
    privilege_boundary_valid: bool
    issues: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """Whether this connection is safe to use for the requested lane."""

        return self.schema_current and self.privilege_boundary_valid and not self.issues
