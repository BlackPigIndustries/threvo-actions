"""Database-neutral compatibility metadata for packaged migrations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MigrationPhase(StrEnum):
    """How a migration changes compatibility with a running older runtime."""

    EXPAND = "expand"
    CONTRACT = "contract"


@dataclass(frozen=True)
class MigrationCompatibility:
    """Deployment compatibility declared by one immutable migration."""

    version: int
    filename: str
    phase: MigrationPhase
    compatible_with_previous_runtime: bool
    requires_writer_quiescence: bool


def migrations_requiring_writer_quiescence(
    compatibility: tuple[MigrationCompatibility, ...],
    *,
    applied_versions: tuple[int, ...],
    pending_versions: tuple[int, ...],
) -> tuple[MigrationCompatibility, ...]:
    """Return pending contract migrations that need existing writers stopped.

    An empty migration history is a bootstrap, so there is no supported older
    runtime to drain. Once any packaged version is recorded, every pending
    migration explicitly marked as requiring quiescence must be acknowledged.
    """

    if not applied_versions:
        return ()
    pending = frozenset(pending_versions)
    return tuple(
        migration
        for migration in compatibility
        if migration.version in pending and migration.requires_writer_quiescence
    )
