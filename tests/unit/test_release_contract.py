from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tomllib
from importlib import import_module
from pathlib import Path

import pytest
from scripts.verify_release import (
    _assert_no_private_context,
    _publication_source_paths,
    record_candidate,
    verify_candidate,
    verify_metadata,
)

import threvo_actions
import threvo_actions.experimental as experimental

ROOT = Path(__file__).parents[2]


def _workflow_run_bodies(workflow: str) -> tuple[str, ...]:
    lines = workflow.splitlines()
    bodies: list[str] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(?P<indent>\s*)(?:-\s+)?run:\s*(?P<inline>.*)$", line)
        if match is None:
            continue
        indent = len(match.group("indent"))
        body = [match.group("inline")]
        for following in lines[index + 1 :]:
            following_indent = len(following) - len(following.lstrip())
            if following.strip() and following_indent <= indent:
                break
            body.append(following)
        bodies.append("\n".join(body))
    return tuple(bodies)


def test_release_metadata_is_consistent() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    skill = (ROOT / ".agents/skills/threvo-actions/SKILL.md").read_text()
    changelog = (ROOT / "CHANGELOG.md").read_text()

    assert project["version"] == threvo_actions.__version__
    assert f'version: "{threvo_actions.__version__}"' in skill
    assert f"## [{threvo_actions.__version__}] - " in changelog


def test_experimental_authoring_surface_stays_namespaced() -> None:
    expected = {
        "ActionApplication",
        "ActionApplicationError",
        "ActionComponents",
        "ActionInspection",
        "ActionIssueCode",
        "ActionOwnershipInspection",
        "ActionRecipe",
        "ActionSettingsInspection",
        "ActionSpec",
        "BoundaryModelInspection",
        "BoundAction",
        "DependencyScopeFactory",
        "RegisteredAction",
    }

    assert set(experimental.__all__) == expected
    assert expected.isdisjoint(threvo_actions.__all__)


def test_experimental_compatibility_window_is_explicit() -> None:
    versioning = (ROOT / "docs/versioning.md").read_text()

    for requirement in (
        "threvo_actions.experimental",
        "120 days",
        "support",
        "revision",
        "retirement",
        "stable promotion",
    ):
        assert requirement in versioning


def test_release_builds_one_reviewed_candidate_and_promotes_same_bytes() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()

    assert "workflow_dispatch:" in workflow
    assert 'tags: ["v[0-9]+.[0-9]+.[0-9]+"]' not in workflow
    assert workflow.count("uv build") == 1
    assert "CANDIDATE.json" in workflow
    assert "candidate-source-commit" in workflow
    assert "getWorkflowRun" in workflow
    assert "Candidate qualification did not pass" in workflow
    assert "name: release-review" in workflow
    assert "must point to the candidate source commit" in workflow
    assert "sha256sum --check --strict" in workflow
    assert "Promotion gate: **passed**" in workflow
    assert "release-distributions" in workflow


def test_release_dispatch_values_never_enter_shell_source() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()

    assert "${{ inputs.release_tag }}" not in "\n".join(_workflow_run_bodies(workflow))
    assert workflow.count("^v[0-9]+\\.[0-9]+\\.[0-9]+$") == 2


@pytest.mark.parametrize(
    "release_tag",
    (
        "v0.1.4$(touch injected)",
        "v0.1.4`touch injected`",
        'v0.1.4"; touch injected; echo "',
        "v0.1.4\ninvalid",
    ),
)
def test_release_verifier_rejects_tag_shell_metacharacters(release_tag: str) -> None:
    with pytest.raises(ValueError, match="tag does not match package version"):
        verify_metadata(expected_tag=release_tag)


def test_candidate_record_rejects_changed_package_bytes(tmp_path: Path) -> None:
    release = tmp_path / "release"
    packages = release / "packages"
    packages.mkdir(parents=True)
    wheel = packages / "threvo_actions-0.1.4-py3-none-any.whl"
    source = packages / "threvo_actions-0.1.4.tar.gz"
    wheel.write_bytes(b"wheel")
    source.write_bytes(b"source")
    (release / "SHA256SUMS").write_text(
        f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  {wheel.name}\n"
        f"{hashlib.sha256(source.read_bytes()).hexdigest()}  {source.name}\n"
    )
    commit = "a" * 40

    record_candidate(release, source_commit=commit, release_tag="v0.1.4")
    verify_candidate(release, source_commit=commit, release_tag="v0.1.4")

    wheel.write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest differs"):
        verify_candidate(release, source_commit=commit, release_tag="v0.1.4")


def test_release_requires_tag_commit_to_already_be_on_main() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()

    assert "git fetch --no-tags origin main:refs/remotes/origin/main" in workflow
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in workflow
    assert "Release tags must point to a commit already contained in main." in workflow


def test_release_actions_are_pinned_to_immutable_commits() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    action_references = re.findall(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", workflow, re.MULTILINE)

    assert action_references
    for reference in action_references:
        action, separator, revision = reference.partition("@")
        assert separator == "@", reference
        assert "/" in action, reference
        assert re.fullmatch(r"[0-9a-f]{40}", revision), reference


def test_release_verifier_rejects_private_context_fingerprints() -> None:
    normalized_marker = b"syntheticprivatehostmarker"
    fingerprints = {
        len(normalized_marker): frozenset({hashlib.sha256(normalized_marker).hexdigest()})
    }

    variants = (
        b"synthetic private host marker",
        b"SYNTHETIC_private-host-marker",
        b"synthetic<!-- hidden -->private\nhost marker",
        b"synthetic&#x70;rivate host marker",
    )
    for variant in variants:
        with pytest.raises(ValueError, match="private host-application context"):
            _assert_no_private_context([variant], fingerprints=fingerprints)


def test_release_verifier_accepts_public_developer_content() -> None:
    normalized_marker = b"syntheticprivatehostmarker"
    fingerprints = {
        len(normalized_marker): frozenset({hashlib.sha256(normalized_marker).hexdigest()})
    }

    _assert_no_private_context(
        [b"Framework-neutral financial action documentation."],
        fingerprints=fingerprints,
    )


def test_publication_source_scan_covers_every_tracked_file() -> None:
    git = shutil.which("git")
    assert git is not None
    tracked = (
        subprocess.run(  # noqa: S603 -- fixed git command against the repository root.
            [git, "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\0")
    )

    scanned = {path.relative_to(ROOT).as_posix() for path in _publication_source_paths()}
    tracked_files = {relative for relative in tracked if relative and (ROOT / relative).is_file()}

    assert tracked_files <= scanned


def test_0_1_public_root_contract_is_frozen() -> None:
    expected = {
        "Action",
        "ActionConfigurationError",
        "ActionDefinition",
        "ActionNotRegisteredError",
        "ActionOperationResult",
        "ActionRegistry",
        "ActionRuntime",
        "ActionStore",
        "ActionType",
        "AnyApproval",
        "ApprovalReasonCode",
        "AuthoritativeTarget",
        "AuthorityBinding",
        "AuthorityDecision",
        "AuthorityEvaluation",
        "AuthorityEvaluatorPort",
        "AuthorityEvidence",
        "AuthorityReceipt",
        "AuthorityReceiptStatus",
        "AuthorityValidationFailure",
        "AuthorityValidationResult",
        "AuthorizationDeniedError",
        "AuthorizationPort",
        "AuthorizationResult",
        "CanonicalizationError",
        "Clock",
        "CommitmentProvider",
        "ConfirmingAuthority",
        "DecisionContext",
        "DefinitionConformanceError",
        "DefinitionTypeMismatchError",
        "DuplicateActionError",
        "EffectClaimResult",
        "EffectKind",
        "EventSink",
        "EvidenceConsumer",
        "ExecutionContext",
        "ExecutionReceipt",
        "ExecutionReceiptStatus",
        "ExecutionResult",
        "ExecutionStatus",
        "ExternalReference",
        "GovernedExecutor",
        "GovernedExecutorPort",
        "IdentifierProvider",
        "InvalidActionResultError",
        "InvalidAuthorityEvidenceError",
        "ItemOutcome",
        "ItemOutcomeStatus",
        "KeyedCommitment",
        "LifecycleStatus",
        "MOfNApprovals",
        "MemoryActionStore",
        "Money",
        "NoopEventSink",
        "OperationOutcome",
        "Participant",
        "PreparationContext",
        "PreparationPort",
        "PreparedAction",
        "ProposalAlreadyExistsError",
        "ProposalNotFoundError",
        "ProposalReceipt",
        "ProposalReceiptStatus",
        "ProposalView",
        "ProposingAgent",
        "ProtectedPayload",
        "ProtectionCodec",
        "ReadContext",
        "Receipt",
        "RequestingPrincipal",
        "ResolvedState",
        "RetentionPort",
        "RetentionStore",
        "RetentionStoreUnavailableError",
        "RuntimeAttributionError",
        "RuntimeEvent",
        "RuntimeEventType",
        "RuntimeReasonCode",
        "SingleApproval",
        "StateResolverPort",
        "StoreInvariantError",
        "StoredProposal",
        "SystemClock",
        "UuidIdentifiers",
        "VerificationReceipt",
        "VerificationReceiptStatus",
        "VerificationResult",
        "VerificationStatus",
        "VerifierPort",
        "assert_definition_conforms",
        "authority_evidence_matches_binding",
        "canonicalize_v1",
        "commitment_payload_v1",
        "resolve_runtime_revision",
        "validate_authority_evidence",
        "validate_proposal_create",
        "validate_proposal_update",
    }

    assert set(threvo_actions.__all__) == expected
    assert all(hasattr(threvo_actions, name) for name in expected)


def test_0_1_documented_adapter_contracts_remain_importable() -> None:
    documented = {
        "threvo_actions.testing": {
            "EphemeralProtection",
            "FixedClock",
            "RecordingEventSink",
            "SequentialIdentifiers",
        },
        "threvo_actions.migrations": {
            "check_postgres_readiness",
            "ConnectionSource",
            "InvalidSchemaNameError",
            "MigrationStateError",
            "MigrationStatus",
            "PostgresMigrationSQL",
            "inspect_postgres",
            "migrate_postgres",
            "plan_postgres_migrations",
            "postgres_migration_compatibility",
            "quote_schema_name",
            "render_postgres_grants",
            "render_postgres_migration_script",
        },
        "threvo_actions.migration_compatibility": {
            "MigrationCompatibility",
            "MigrationPhase",
            "migrations_requiring_writer_quiescence",
        },
        "threvo_actions.mysql_migrations": {
            "check_mysql_readiness",
            "MySQLConnectionSource",
            "MySQLMigrationStateError",
            "MySQLMigrationStatus",
            "inspect_mysql",
            "migrate_mysql",
            "mysql_migration_compatibility",
            "render_mysql_grants",
        },
        "threvo_actions.sqlite_migrations": {
            "SQLiteMigrationStateError",
            "SQLiteMigrationStatus",
            "inspect_sqlite",
            "migrate_sqlite",
            "sqlite_migration_compatibility",
        },
        "threvo_actions.readiness": {
            "DatabaseAccessLane",
            "DatabaseAdapter",
            "DatabaseReadiness",
        },
        "threvo_actions.store_security": {
            "MYSQL_STORE_SECURITY_PROFILE",
            "POSTGRESQL_STORE_SECURITY_PROFILE",
            "SQLITE_STORE_SECURITY_PROFILE",
            "StorePrivilegeBoundary",
            "StoreGuarantee",
            "StoreGuaranteeEnforcement",
            "StoreGuaranteeLevel",
            "StoreSecurityProfile",
            "StoreSupportTier",
            "StoreWriterTopology",
            "official_store_security_profiles",
        },
        "threvo_actions.stores.postgres": {
            "ConnectionSource",
            "PostgresActionStore",
            "PostgresRetentionStore",
            "StoredDataCorruptionError",
        },
        "threvo_actions.stores.mysql": {
            "MySQLActionStore",
            "MySQLAdapterLimitError",
            "MySQLConnectionSource",
            "MySQLRetentionStore",
            "MySQLStoredDataCorruptionError",
        },
        "threvo_actions.stores.sqlite": {
            "SQLiteActionStore",
            "SQLiteRetentionStore",
            "SQLiteStoredDataCorruptionError",
        },
    }

    for module_name, names in documented.items():
        module = import_module(module_name)
        assert all(hasattr(module, name) for name in names)
