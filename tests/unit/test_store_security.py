from __future__ import annotations

from threvo_actions.readiness import DatabaseAdapter
from threvo_actions.store_security import (
    MYSQL_STORE_SECURITY_PROFILE,
    POSTGRESQL_STORE_SECURITY_PROFILE,
    SQLITE_STORE_SECURITY_PROFILE,
    StorePrivilegeBoundary,
    StoreSupportTier,
    StoreWriterTopology,
    official_store_security_profiles,
)


def test_official_profiles_have_unique_stable_identities() -> None:
    profiles = official_store_security_profiles()

    assert profiles == (
        POSTGRESQL_STORE_SECURITY_PROFILE,
        MYSQL_STORE_SECURITY_PROFILE,
        SQLITE_STORE_SECURITY_PROFILE,
    )
    assert len({profile.identifier for profile in profiles}) == len(profiles)
    assert {profile.adapter for profile in profiles} == set(DatabaseAdapter)


def test_all_profiles_state_the_external_data_protection_boundary() -> None:
    for profile in official_store_security_profiles():
        assert profile.requires_host_protected_private_state
        assert not profile.adapter_manages_at_rest_encryption
        assert not profile.adapter_authenticates_evidence_issuers
        assert not profile.adapter_erases_external_copies
        assert profile.independent_connection_conformance
        assert profile.limitations


def test_production_profiles_require_database_role_separation() -> None:
    for profile in (POSTGRESQL_STORE_SECURITY_PROFILE, MYSQL_STORE_SECURITY_PROFILE):
        assert profile.support_tier is StoreSupportTier.PRODUCTION_ORIENTED_OFFICIAL
        assert profile.writer_topology is StoreWriterTopology.MULTI_PROCESS
        assert profile.privilege_boundary is StorePrivilegeBoundary.DATABASE_ROLES


def test_sqlite_profile_does_not_overclaim_production_safety() -> None:
    assert SQLITE_STORE_SECURITY_PROFILE.support_tier is StoreSupportTier.BOUNDED_USE_OFFICIAL
    assert (
        SQLITE_STORE_SECURITY_PROFILE.writer_topology is StoreWriterTopology.BOUNDED_SINGLE_WRITER
    )
    assert SQLITE_STORE_SECURITY_PROFILE.privilege_boundary is StorePrivilegeBoundary.PROCESS_ONLY
