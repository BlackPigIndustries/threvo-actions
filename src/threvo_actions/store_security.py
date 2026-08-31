"""Machine-readable security boundaries for official action stores."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .readiness import DatabaseAdapter


class StoreSupportTier(StrEnum):
    """The operational scope qualified by this project."""

    PRODUCTION_ORIENTED_OFFICIAL = "production_oriented_official"
    BOUNDED_USE_OFFICIAL = "bounded_use_official"


class StoreWriterTopology(StrEnum):
    """The writer topology a profile is designed to support."""

    MULTI_PROCESS = "multi_process"
    BOUNDED_SINGLE_WRITER = "bounded_single_writer"


class StorePrivilegeBoundary(StrEnum):
    """How runtime and retention authority are separated."""

    DATABASE_ROLES = "database_roles"
    PROCESS_ONLY = "process_only"


class StoreGuarantee(StrEnum):
    """A persistence guarantee that an adapter may enforce at different levels."""

    LIFECYCLE_TRANSITIONS = "lifecycle_transitions"
    ATOMIC_EFFECT_ADMISSION = "atomic_effect_admission"
    APPEND_ONLY_EVIDENCE = "append_only_evidence"
    ROLE_SEPARATED_ERASURE = "role_separated_erasure"


class StoreGuaranteeLevel(StrEnum):
    """The boundary responsible for enforcing one persistence guarantee."""

    DATABASE_ENGINE = "database_engine"
    ADAPTER_PROCESS = "adapter_process"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class StoreGuaranteeEnforcement:
    """The enforcement level claimed for one qualified store guarantee."""

    guarantee: StoreGuarantee
    level: StoreGuaranteeLevel


@dataclass(frozen=True)
class StoreSecurityProfile:
    """Data-handling and operating claims for one store configuration."""

    identifier: str
    adapter: DatabaseAdapter
    support_tier: StoreSupportTier
    writer_topology: StoreWriterTopology
    privilege_boundary: StorePrivilegeBoundary
    guarantee_enforcement: tuple[StoreGuaranteeEnforcement, ...]
    qualification_targets: tuple[str, ...]
    independent_connection_conformance: bool
    requires_host_protected_private_state: bool
    adapter_manages_at_rest_encryption: bool
    adapter_authenticates_evidence_issuers: bool
    adapter_erases_external_copies: bool
    limitations: tuple[str, ...]


_DATABASE_ENGINE_GUARANTEES = tuple(
    StoreGuaranteeEnforcement(guarantee, StoreGuaranteeLevel.DATABASE_ENGINE)
    for guarantee in StoreGuarantee
)


POSTGRESQL_STORE_SECURITY_PROFILE = StoreSecurityProfile(
    identifier="postgresql/v1",
    adapter=DatabaseAdapter.POSTGRESQL,
    support_tier=StoreSupportTier.PRODUCTION_ORIENTED_OFFICIAL,
    writer_topology=StoreWriterTopology.MULTI_PROCESS,
    privilege_boundary=StorePrivilegeBoundary.DATABASE_ROLES,
    guarantee_enforcement=_DATABASE_ENGINE_GUARANTEES,
    qualification_targets=("PostgreSQL 15", "PostgreSQL 16"),
    independent_connection_conformance=True,
    requires_host_protected_private_state=True,
    adapter_manages_at_rest_encryption=False,
    adapter_authenticates_evidence_issuers=False,
    adapter_erases_external_copies=False,
    limitations=(
        "logical erasure does not erase WAL, replicas, snapshots, exports, or backups",
        "target-side idempotency and authoritative verification remain host responsibilities",
    ),
)

MYSQL_STORE_SECURITY_PROFILE = StoreSecurityProfile(
    identifier="mysql/v1",
    adapter=DatabaseAdapter.MYSQL,
    support_tier=StoreSupportTier.PRODUCTION_ORIENTED_OFFICIAL,
    writer_topology=StoreWriterTopology.MULTI_PROCESS,
    privilege_boundary=StorePrivilegeBoundary.DATABASE_ROLES,
    guarantee_enforcement=_DATABASE_ENGINE_GUARANTEES,
    qualification_targets=("MySQL 8.0", "MySQL 8.4"),
    independent_connection_conformance=True,
    requires_host_protected_private_state=True,
    adapter_manages_at_rest_encryption=False,
    adapter_authenticates_evidence_issuers=False,
    adapter_erases_external_copies=False,
    limitations=(
        "logical erasure does not erase binary logs, undo history, replicas, exports, or backups",
        "MariaDB and MySQL 5.7 are outside the official profile",
    ),
)

SQLITE_STORE_SECURITY_PROFILE = StoreSecurityProfile(
    identifier="sqlite/v1",
    adapter=DatabaseAdapter.SQLITE,
    support_tier=StoreSupportTier.BOUNDED_USE_OFFICIAL,
    writer_topology=StoreWriterTopology.BOUNDED_SINGLE_WRITER,
    privilege_boundary=StorePrivilegeBoundary.PROCESS_ONLY,
    guarantee_enforcement=(
        StoreGuaranteeEnforcement(
            StoreGuarantee.LIFECYCLE_TRANSITIONS,
            StoreGuaranteeLevel.DATABASE_ENGINE,
        ),
        StoreGuaranteeEnforcement(
            StoreGuarantee.ATOMIC_EFFECT_ADMISSION,
            StoreGuaranteeLevel.DATABASE_ENGINE,
        ),
        StoreGuaranteeEnforcement(
            StoreGuarantee.APPEND_ONLY_EVIDENCE,
            StoreGuaranteeLevel.ADAPTER_PROCESS,
        ),
        StoreGuaranteeEnforcement(
            StoreGuarantee.ROLE_SEPARATED_ERASURE,
            StoreGuaranteeLevel.UNSUPPORTED,
        ),
    ),
    qualification_targets=("CPython sqlite3 on Python 3.11-3.13",),
    independent_connection_conformance=True,
    requires_host_protected_private_state=True,
    adapter_manages_at_rest_encryption=False,
    adapter_authenticates_evidence_issuers=False,
    adapter_erases_external_copies=False,
    limitations=(
        "no database-role separation between runtime and retention",
        "logical erasure does not erase free pages, journals, WAL files, snapshots, or backups",
        "not qualified for general multi-worker financial production use",
    ),
)


def official_store_security_profiles() -> tuple[StoreSecurityProfile, ...]:
    """Return the immutable profiles maintained by this project."""

    return (
        POSTGRESQL_STORE_SECURITY_PROFILE,
        MYSQL_STORE_SECURITY_PROFILE,
        SQLITE_STORE_SECURITY_PROFILE,
    )
