"""Reusable conformance helpers for action stores, hosts, and safe projections."""

from __future__ import annotations

import asyncio
import hashlib
import math
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence, Set
from dataclasses import dataclass, fields, is_dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

from pydantic import BaseModel, TypeAdapter

from .canonical import ProposalBoundCommitmentProvider, ProposalBoundProtectionCodec
from .models import LifecycleStatus, SafeReference
from .receipts import AuthorityReceipt, AuthorityReceiptStatus
from .runtime import OperationOutcome
from .stores.base import (
    ALLOWED_LIFECYCLE_TRANSITIONS,
    EffectClaimResult,
    StoreInvariantError,
)

if TYPE_CHECKING:
    from datetime import datetime

    from .authority import AuthorityEvidence
    from .canonical import CommitmentProviderPort, ProtectionCodecPort
    from .runtime import ActionOperationResult
    from .stores.base import ActionStore, RetentionStore, StoredProposal

T = TypeVar("T")
_SAFE_REFERENCE_ADAPTER = TypeAdapter(SafeReference)


class ConformanceError(AssertionError):
    """A stable, secret-free conformance failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _derived_safe_reference(source: str, *, purpose: str, discriminator: str) -> str:
    digest = hashlib.sha256(f"{purpose}\0{source}\0{discriminator}".encode()).hexdigest()
    return _SAFE_REFERENCE_ADAPTER.validate_python(f"conformance:{purpose}:{digest}")


@dataclass(frozen=True)
class LeakageFinding:
    """Location and caller-supplied label for sensitive data, never its value."""

    path: str
    label: str
    kind: str


@dataclass(frozen=True)
class StoreConformanceCase:
    store: ActionStore
    retention_store: RetentionStore
    original: StoredProposal
    evidence: AuthorityEvidence
    observed_at: datetime


@dataclass(frozen=True)
class IndependentStoreConformanceCase:
    """Two adapters backed by independently created connections to one store."""

    first_store: ActionStore
    second_store: ActionStore
    original: StoredProposal
    evidence: AuthorityEvidence
    observed_at: datetime
    security_profile_identifier: str

    def __post_init__(self) -> None:
        if not self.security_profile_identifier.strip():
            raise ValueError("security_profile_identifier must not be empty")


@dataclass(frozen=True)
class IndependentStoreConformanceReport:
    """Deterministic evidence emitted after all independent-store checks pass."""

    security_profile_identifier: str
    checks: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkResult:
    profile: str
    iterations: int
    p50_ms: float
    p95_ms: float
    p99_ms: float


@dataclass(frozen=True)
class PerformanceProfile:
    name: str
    max_p99_ms: float
    max_p95_ms: float | None = None
    min_iterations: int = 100

    def __post_init__(self) -> None:
        if self.max_p99_ms <= 0:
            raise ValueError("max_p99_ms must be positive")
        if self.max_p95_ms is not None and self.max_p95_ms <= 0:
            raise ValueError("max_p95_ms must be positive when provided")
        if self.min_iterations <= 0:
            raise ValueError("min_iterations must be positive")


class RuntimeConformanceDriver(Protocol):
    """Small host-owned driver used by the reusable runtime scenarios."""

    @property
    def executor_calls(self) -> int: ...

    async def prepare(self) -> ActionOperationResult: ...

    async def record_approval(self, proposal_reference: str) -> ActionOperationResult: ...

    async def execute(self, proposal_reference: str) -> ActionOperationResult: ...

    async def reconcile(self, proposal_reference: str) -> ActionOperationResult: ...

    async def make_verification_due(self) -> None: ...

    async def revoke_execution_authorization(self) -> None: ...

    async def introduce_material_drift(self) -> None: ...


def find_sensitive_data(
    value: object,
    *,
    forbidden_literals: Mapping[str, str],
    forbidden_key_fragments: Collection[str] = (),
) -> tuple[LeakageFinding, ...]:
    """Recursively locate seeded secrets without echoing them in the report."""

    findings: list[LeakageFinding] = []
    seen: set[int] = set()
    normalized_fragments = tuple(fragment.casefold() for fragment in forbidden_key_fragments)

    def inspect_value(current: object, path: str) -> None:
        if isinstance(current, (str, bytes)):
            text = (
                current.decode("utf-8", errors="replace") if isinstance(current, bytes) else current
            )
            for label, literal in forbidden_literals.items():
                if literal and literal in text:
                    findings.append(LeakageFinding(path=path, label=label, kind="literal"))
            return
        if current is None or isinstance(current, (bool, int, float)):
            return

        identity = id(current)
        if identity in seen:
            return
        seen.add(identity)

        if isinstance(current, BaseModel):
            for field_name, field_value in current:
                inspect_key(field_name, f"{path}.{field_name}")
                inspect_value(field_value, f"{path}.{field_name}")
            return
        if isinstance(current, BaseException):
            inspect_value(current.args, f"{path}.args")
            inspect_value(vars(current), f"{path}.attributes")
            return
        if is_dataclass(current) and not isinstance(current, type):
            for field in fields(current):
                inspect_key(field.name, f"{path}.{field.name}")
                inspect_value(getattr(current, field.name), f"{path}.{field.name}")
            return
        if isinstance(current, Mapping):
            for index, (key, item) in enumerate(current.items()):
                entry_path = f"{path}[{index}]"
                inspect_key(key, f"{entry_path}.key")
                inspect_value(key, f"{entry_path}.key")
                inspect_value(item, f"{entry_path}.value")
            return
        if isinstance(current, Sequence):
            for index, item in enumerate(current):
                inspect_value(item, f"{path}[{index}]")
            return
        if isinstance(current, Set):
            for item in current:
                inspect_value(item, f"{path}[set-item]")

    def inspect_key(key: object, path: str) -> None:
        if not isinstance(key, str):
            return
        normalized_key = key.casefold()
        for fragment in normalized_fragments:
            if fragment in normalized_key:
                findings.append(LeakageFinding(path=path, label=f"key:{fragment}", kind="key"))

    inspect_value(value, "$")
    return tuple(sorted(findings, key=lambda finding: (finding.path, finding.label, finding.kind)))


def assert_no_sensitive_data(
    value: object,
    *,
    forbidden_literals: Mapping[str, str],
    forbidden_key_fragments: Collection[str] = (),
) -> None:
    """Fail with labels and structural paths while keeping seeded values secret."""

    findings = find_sensitive_data(
        value,
        forbidden_literals=forbidden_literals,
        forbidden_key_fragments=forbidden_key_fragments,
    )
    if findings:
        summary = ",".join(f"{finding.label}@{finding.path}" for finding in findings)
        raise ConformanceError(f"sensitive_data:{summary}")


async def assert_action_store_conforms(case: StoreConformanceCase) -> None:
    """Exercise tenant isolation, guarded updates, and atomic effect admission."""

    await case.store.create(case.original)
    competitor_reference = _derived_safe_reference(
        case.original.proposal_reference,
        purpose="proposal-competitor",
        discriminator="primary",
    )
    competitor = case.original.model_copy(
        update={
            "proposal_reference": competitor_reference,
            "protected_private_snapshot": (
                case.original.protected_private_snapshot.model_copy(
                    update={
                        "key_handle": _derived_safe_reference(
                            case.original.protected_private_snapshot.key_handle,
                            purpose="payload-key",
                            discriminator="competitor",
                        )
                    }
                )
                if case.original.protected_private_snapshot is not None
                else None
            ),
            "commitment": (
                case.original.commitment.model_copy(
                    update={
                        "key_handle": _derived_safe_reference(
                            case.original.commitment.key_handle,
                            purpose="commitment-key",
                            discriminator="competitor",
                        ),
                        "digest": _derived_safe_reference(
                            case.original.commitment.digest,
                            purpose="commitment-digest",
                            discriminator="competitor",
                        ),
                    }
                )
                if case.original.commitment is not None
                else None
            ),
        }
    )
    competitor_evidence = case.evidence.model_copy(
        update={
            "proposal_instance_reference": competitor.proposal_reference,
            "proposal_commitment": (
                competitor.commitment.digest if competitor.commitment is not None else "missing"
            ),
        }
    )
    await case.store.create(competitor)
    _require(
        await case.store.get(
            case.original.tenant_reference,
            case.original.proposal_reference,
        )
        == case.original,
        "store_round_trip",
    )
    _require(
        await case.store.get("tenant:conformance-other", case.original.proposal_reference) is None,
        "store_tenant_isolation",
    )
    await _assert_store_update_invariants(case)

    authorized = case.original.model_copy(
        update={
            "authority_evidence": (case.evidence,),
            "lifecycle_status": LifecycleStatus.AUTHORIZED,
            "revision": case.original.revision + 1,
        }
    )
    _require(
        not await case.store.compare_and_set(
            tenant_reference="tenant:conformance-other",
            proposal_reference=case.original.proposal_reference,
            expected_revision=case.original.revision,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=authorized,
        ),
        "store_foreign_tenant_update",
    )
    _require(
        await case.store.admit_execution(
            tenant_reference="tenant:conformance-other",
            proposal_reference=case.original.proposal_reference,
            expected_revision=case.original.revision,
            admitted_at=case.observed_at,
            updated=authorized,
        )
        is EffectClaimResult.PROPOSAL_NOT_FOUND,
        "store_foreign_tenant_admission",
    )
    _require(
        not await case.retention_store.mark_erasure_pending(
            tenant_reference="tenant:conformance-other",
            proposal_reference=case.original.proposal_reference,
            expected_revision=case.original.revision,
            pending_at=case.observed_at,
        ),
        "store_foreign_tenant_erasure",
    )
    _require(
        await case.store.get_effect_claim_owner(
            tenant_reference="tenant:conformance-other",
            action_type=case.original.action_type,
            semantic_effect_reference=case.original.semantic_effect_reference,
        )
        is None,
        "store_foreign_tenant_effect_owner",
    )
    _require(
        await case.store.get(
            case.original.tenant_reference,
            case.original.proposal_reference,
        )
        == case.original,
        "store_foreign_tenant_no_mutation",
    )
    _require(
        await case.store.compare_and_set(
            tenant_reference=case.original.tenant_reference,
            proposal_reference=case.original.proposal_reference,
            expected_revision=case.original.revision,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=authorized,
        ),
        "store_guarded_update",
    )
    competitor_authorized = competitor.model_copy(
        update={
            "authority_evidence": (competitor_evidence,),
            "lifecycle_status": LifecycleStatus.AUTHORIZED,
            "revision": competitor.revision + 1,
        }
    )
    _require(
        await case.store.compare_and_set(
            tenant_reference=competitor.tenant_reference,
            proposal_reference=competitor.proposal_reference,
            expected_revision=competitor.revision,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=competitor_authorized,
        ),
        "store_competing_authorization",
    )
    _require(
        not await case.store.compare_and_set(
            tenant_reference=case.original.tenant_reference,
            proposal_reference=case.original.proposal_reference,
            expected_revision=case.original.revision,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=authorized,
        ),
        "store_stale_revision",
    )

    executing = authorized.model_copy(
        update={
            "lifecycle_status": LifecycleStatus.EXECUTING,
            "revision": authorized.revision + 1,
        }
    )
    competitor_executing = competitor_authorized.model_copy(
        update={
            "lifecycle_status": LifecycleStatus.EXECUTING,
            "revision": competitor_authorized.revision + 1,
        }
    )
    admissions = await asyncio.gather(
        case.store.admit_execution(
            tenant_reference=case.original.tenant_reference,
            proposal_reference=case.original.proposal_reference,
            expected_revision=authorized.revision,
            admitted_at=case.observed_at,
            updated=executing,
        ),
        case.store.admit_execution(
            tenant_reference=competitor.tenant_reference,
            proposal_reference=competitor.proposal_reference,
            expected_revision=competitor_authorized.revision,
            admitted_at=case.observed_at,
            updated=competitor_executing,
        ),
    )
    _require(
        admissions.count(EffectClaimResult.ACQUIRED) == 1
        and admissions.count(EffectClaimResult.CONFLICT) == 1,
        "store_atomic_effect_admission",
    )
    winner_index = admissions.index(EffectClaimResult.ACQUIRED)
    winner = (executing, competitor_executing)[winner_index]
    _require(
        await case.store.get_effect_claim_owner(
            tenant_reference=case.original.tenant_reference,
            action_type=case.original.action_type,
            semantic_effect_reference=case.original.semantic_effect_reference,
        )
        == winner.proposal_reference,
        "store_effect_owner",
    )
    _require(
        not await case.retention_store.mark_erasure_pending(
            tenant_reference=winner.tenant_reference,
            proposal_reference=winner.proposal_reference,
            expected_revision=winner.revision,
            pending_at=case.observed_at,
        ),
        "store_active_effect_not_erasable",
    )

    loser = (competitor_authorized, authorized)[winner_index]
    stale_winner = winner.model_copy(
        update={
            "lifecycle_status": LifecycleStatus.STALE,
            "revision": winner.revision + 1,
        }
    )
    _require(
        await case.store.compare_and_set(
            tenant_reference=winner.tenant_reference,
            proposal_reference=winner.proposal_reference,
            expected_revision=winner.revision,
            expected_statuses=(LifecycleStatus.EXECUTING,),
            updated=stale_winner,
        ),
        "store_stale_no_effect_settlement",
    )
    replacement_executing = loser.model_copy(
        update={
            "lifecycle_status": LifecycleStatus.EXECUTING,
            "revision": loser.revision + 1,
        }
    )
    _require(
        await case.store.admit_execution(
            tenant_reference=loser.tenant_reference,
            proposal_reference=loser.proposal_reference,
            expected_revision=loser.revision,
            admitted_at=case.observed_at,
            updated=replacement_executing,
        )
        is EffectClaimResult.ACQUIRED,
        "store_stale_effect_claim_transfer",
    )
    _require(
        await case.store.get_effect_claim_owner(
            tenant_reference=loser.tenant_reference,
            action_type=loser.action_type,
            semantic_effect_reference=loser.semantic_effect_reference,
        )
        == loser.proposal_reference,
        "store_transferred_effect_owner",
    )

    verification_pending = replacement_executing.model_copy(
        update={
            "lifecycle_status": LifecycleStatus.VERIFICATION_PENDING,
            "revision": replacement_executing.revision + 1,
        }
    )
    _require(
        await case.store.compare_and_set(
            tenant_reference=loser.tenant_reference,
            proposal_reference=loser.proposal_reference,
            expected_revision=replacement_executing.revision,
            expected_statuses=(LifecycleStatus.EXECUTING,),
            updated=verification_pending,
        ),
        "store_effect_owner_verification_pending",
    )
    verified = verification_pending.model_copy(
        update={
            "lifecycle_status": LifecycleStatus.VERIFIED,
            "revision": verification_pending.revision + 1,
        }
    )
    _require(
        await case.store.compare_and_set(
            tenant_reference=loser.tenant_reference,
            proposal_reference=loser.proposal_reference,
            expected_revision=verification_pending.revision,
            expected_statuses=(LifecycleStatus.VERIFICATION_PENDING,),
            updated=verified,
        ),
        "store_effect_owner_verified",
    )

    terminal_reference = _derived_safe_reference(
        competitor.proposal_reference,
        purpose="proposal-terminal",
        discriminator="terminal",
    )
    terminal_contender = competitor.model_copy(
        update={
            "proposal_reference": terminal_reference,
            "protected_private_snapshot": (
                competitor.protected_private_snapshot.model_copy(
                    update={
                        "key_handle": _derived_safe_reference(
                            competitor.protected_private_snapshot.key_handle,
                            purpose="payload-key",
                            discriminator="terminal",
                        )
                    }
                )
                if competitor.protected_private_snapshot is not None
                else None
            ),
            "commitment": (
                competitor.commitment.model_copy(
                    update={
                        "key_handle": _derived_safe_reference(
                            competitor.commitment.key_handle,
                            purpose="commitment-key",
                            discriminator="terminal",
                        ),
                        "digest": _derived_safe_reference(
                            competitor.commitment.digest,
                            purpose="commitment-digest",
                            discriminator="terminal",
                        ),
                    }
                )
                if competitor.commitment is not None
                else None
            ),
        }
    )
    await case.store.create(terminal_contender)
    terminal_evidence = case.evidence.model_copy(
        update={
            "proposal_instance_reference": terminal_contender.proposal_reference,
            "proposal_commitment": (
                terminal_contender.commitment.digest
                if terminal_contender.commitment is not None
                else "missing"
            ),
        }
    )
    terminal_authorized = terminal_contender.model_copy(
        update={
            "authority_evidence": (terminal_evidence,),
            "lifecycle_status": LifecycleStatus.AUTHORIZED,
            "revision": terminal_contender.revision + 1,
        }
    )
    _require(
        await case.store.compare_and_set(
            tenant_reference=terminal_contender.tenant_reference,
            proposal_reference=terminal_contender.proposal_reference,
            expected_revision=terminal_contender.revision,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=terminal_authorized,
        ),
        "store_terminal_competing_authorization",
    )
    terminal_executing = terminal_authorized.model_copy(
        update={
            "lifecycle_status": LifecycleStatus.EXECUTING,
            "revision": terminal_authorized.revision + 1,
        }
    )
    _require(
        await case.store.admit_execution(
            tenant_reference=terminal_authorized.tenant_reference,
            proposal_reference=terminal_authorized.proposal_reference,
            expected_revision=terminal_authorized.revision,
            admitted_at=case.observed_at,
            updated=terminal_executing,
        )
        is EffectClaimResult.CONFLICT,
        "store_verified_effect_claim_not_transferable",
    )


async def assert_independent_store_connections_conform(
    case: IndependentStoreConformanceCase,
) -> IndependentStoreConformanceReport:
    """Prove shared visibility, guarded revisions, and effect admission across stores."""

    cas_proposal = _independent_proposal(case.original, "cas")
    cas_evidence = _independent_evidence(case.evidence, cas_proposal)
    await case.first_store.create(cas_proposal)
    _require(
        await case.second_store.get(
            cas_proposal.tenant_reference,
            cas_proposal.proposal_reference,
        )
        == cas_proposal,
        "independent_store_shared_visibility",
    )
    cas_authorized = cas_proposal.model_copy(
        update={
            "authority_evidence": (cas_evidence,),
            "lifecycle_status": LifecycleStatus.AUTHORIZED,
            "revision": cas_proposal.revision + 1,
        }
    )
    cas_results = await asyncio.gather(
        *(
            store.compare_and_set(
                tenant_reference=cas_proposal.tenant_reference,
                proposal_reference=cas_proposal.proposal_reference,
                expected_revision=cas_proposal.revision,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=cas_authorized,
            )
            for store in (case.first_store, case.second_store)
        )
    )
    _require(
        cas_results.count(True) == 1 and cas_results.count(False) == 1,
        "independent_store_atomic_compare_and_set",
    )
    _require(
        await case.first_store.get(
            cas_proposal.tenant_reference,
            cas_proposal.proposal_reference,
        )
        == cas_authorized
        and await case.second_store.get(
            cas_proposal.tenant_reference,
            cas_proposal.proposal_reference,
        )
        == cas_authorized,
        "independent_store_revision_visibility",
    )

    first_proposal = _independent_proposal(case.original, "effect:first")
    second_proposal = _independent_proposal(case.original, "effect:second")
    authorized: list[StoredProposal] = []
    for store, proposal in zip(
        (case.first_store, case.second_store),
        (first_proposal, second_proposal),
        strict=True,
    ):
        evidence = _independent_evidence(case.evidence, proposal)
        await store.create(proposal)
        current = proposal.model_copy(
            update={
                "authority_evidence": (evidence,),
                "lifecycle_status": LifecycleStatus.AUTHORIZED,
                "revision": proposal.revision + 1,
            }
        )
        _require(
            await store.compare_and_set(
                tenant_reference=proposal.tenant_reference,
                proposal_reference=proposal.proposal_reference,
                expected_revision=proposal.revision,
                expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
                updated=current,
            ),
            "independent_store_effect_setup",
        )
        authorized.append(current)

    executing = tuple(
        current.model_copy(
            update={
                "lifecycle_status": LifecycleStatus.EXECUTING,
                "revision": current.revision + 1,
            }
        )
        for current in authorized
    )
    admission_results = await asyncio.gather(
        *(
            store.admit_execution(
                tenant_reference=current.tenant_reference,
                proposal_reference=current.proposal_reference,
                expected_revision=current.revision,
                admitted_at=case.observed_at,
                updated=updated,
            )
            for store, current, updated in zip(
                (case.first_store, case.second_store),
                authorized,
                executing,
                strict=True,
            )
        )
    )
    _require(
        admission_results.count(EffectClaimResult.ACQUIRED) == 1
        and admission_results.count(EffectClaimResult.CONFLICT) == 1,
        "independent_store_atomic_effect_admission",
    )
    winner = executing[admission_results.index(EffectClaimResult.ACQUIRED)]
    owner_results = await asyncio.gather(
        *(
            store.get_effect_claim_owner(
                tenant_reference=winner.tenant_reference,
                action_type=winner.action_type,
                semantic_effect_reference=winner.semantic_effect_reference,
            )
            for store in (case.first_store, case.second_store)
        )
    )
    _require(
        owner_results == [winner.proposal_reference, winner.proposal_reference],
        "independent_store_effect_owner_visibility",
    )
    return IndependentStoreConformanceReport(
        security_profile_identifier=case.security_profile_identifier,
        checks=(
            "shared_visibility",
            "atomic_compare_and_set",
            "revision_visibility",
            "atomic_effect_admission",
            "effect_owner_visibility",
        ),
    )


async def _assert_store_update_invariants(case: StoreConformanceCase) -> None:
    await _assert_lifecycle_transition_guards(case)

    probe = _conformance_proposal(case, "append-only")
    await case.store.create(probe)
    first_evidence = _conformance_evidence(case, probe)
    second_evidence = first_evidence.model_copy(
        update={
            "channel_assurance": _derived_safe_reference(
                first_evidence.channel_assurance,
                purpose="authority-channel",
                discriminator="second",
            )
        }
    )
    first_receipt = AuthorityReceipt(
        receipt_reference=_derived_safe_reference(
            probe.proposal_reference,
            purpose="receipt",
            discriminator="one",
        ),
        correlation_reference=probe.proposal_reference,
        causation_reference=probe.proposal_reference,
        observed_at=case.observed_at,
        status=AuthorityReceiptStatus.RECORDED,
        participant=first_evidence.authority,
    )
    second_receipt = first_receipt.model_copy(
        update={
            "receipt_reference": _derived_safe_reference(
                probe.proposal_reference,
                purpose="receipt",
                discriminator="two",
            )
        }
    )
    authorized = probe.model_copy(
        update={
            "authority_evidence": (first_evidence, second_evidence),
            "receipts": (first_receipt, second_receipt),
            "lifecycle_status": LifecycleStatus.AUTHORIZED,
            "revision": probe.revision + 1,
        }
    )
    _require(
        await case.store.compare_and_set(
            tenant_reference=probe.tenant_reference,
            proposal_reference=probe.proposal_reference,
            expected_revision=probe.revision,
            expected_statuses=(LifecycleStatus.AWAITING_AUTHORITY,),
            updated=authorized,
        ),
        "store_invariant_probe_setup",
    )
    await _require_update_rejected(
        case,
        current=authorized,
        updated=authorized.model_copy(
            update={
                "authority_evidence": authorized.authority_evidence[:1],
                "revision": authorized.revision + 1,
            }
        ),
        code="store_authority_evidence_append_only",
    )
    await _require_update_rejected(
        case,
        current=authorized,
        updated=authorized.model_copy(
            update={
                "authority_evidence": tuple(reversed(authorized.authority_evidence)),
                "revision": authorized.revision + 1,
            }
        ),
        code="store_authority_evidence_append_only",
    )
    await _require_update_rejected(
        case,
        current=authorized,
        updated=authorized.model_copy(
            update={
                "receipts": authorized.receipts[:1],
                "revision": authorized.revision + 1,
            }
        ),
        code="store_receipts_append_only",
    )
    await _require_update_rejected(
        case,
        current=authorized,
        updated=authorized.model_copy(
            update={
                "receipts": tuple(reversed(authorized.receipts)),
                "revision": authorized.revision + 1,
            }
        ),
        code="store_receipts_append_only",
    )
    await _require_update_rejected(
        case,
        current=authorized,
        updated=authorized.model_copy(
            update={
                "erased_at": case.observed_at,
                "revision": authorized.revision + 1,
            }
        ),
        code="store_content_free_tombstone",
    )


async def _assert_lifecycle_transition_guards(case: StoreConformanceCase) -> None:
    for source in LifecycleStatus:
        current = await _proposal_at_status(case, source)
        allowed = ALLOWED_LIFECYCLE_TRANSITIONS[source]
        for target in LifecycleStatus:
            if target is source or target in allowed:
                continue
            await _require_update_rejected(
                case,
                current=current,
                updated=current.model_copy(
                    update={
                        "lifecycle_status": target,
                        "revision": current.revision + 1,
                    }
                ),
                code=f"store_lifecycle_transition:{source.value}:{target.value}",
            )


async def _proposal_at_status(
    case: StoreConformanceCase,
    status: LifecycleStatus,
) -> StoredProposal:
    paths = _lifecycle_seed_paths()
    current = _conformance_proposal(case, f"transition:{status.value}").model_copy(
        update={"lifecycle_status": LifecycleStatus.AWAITING_AUTHORITY}
    )
    await case.store.create(current)
    for target in paths[status]:
        updated = current.model_copy(
            update={
                "lifecycle_status": target,
                "revision": current.revision + 1,
            }
        )
        if target is LifecycleStatus.EXECUTING:
            result = await case.store.admit_execution(
                tenant_reference=current.tenant_reference,
                proposal_reference=current.proposal_reference,
                expected_revision=current.revision,
                admitted_at=case.observed_at,
                updated=updated,
            )
            _require(
                result
                in {
                    EffectClaimResult.ACQUIRED,
                    EffectClaimResult.OWNED_BY_PROPOSAL,
                },
                "store_transition_probe_execution",
            )
        else:
            _require(
                await case.store.compare_and_set(
                    tenant_reference=current.tenant_reference,
                    proposal_reference=current.proposal_reference,
                    expected_revision=current.revision,
                    expected_statuses=(current.lifecycle_status,),
                    updated=updated,
                ),
                "store_transition_probe_setup",
            )
        current = updated
    return current


def _lifecycle_seed_paths() -> dict[LifecycleStatus, tuple[LifecycleStatus, ...]]:
    authorized = (LifecycleStatus.AUTHORIZED,)
    executing = (*authorized, LifecycleStatus.EXECUTING)
    verification = (*executing, LifecycleStatus.VERIFICATION_PENDING)
    return {
        LifecycleStatus.AWAITING_AUTHORITY: (),
        LifecycleStatus.AUTHORIZED: authorized,
        LifecycleStatus.BLOCKED: (*authorized, LifecycleStatus.BLOCKED),
        LifecycleStatus.DENIED: (LifecycleStatus.DENIED,),
        LifecycleStatus.EXECUTING: executing,
        LifecycleStatus.EXPIRED: (LifecycleStatus.EXPIRED,),
        LifecycleStatus.FAILED_KNOWN: (*executing, LifecycleStatus.FAILED_KNOWN),
        LifecycleStatus.FAILED_UNKNOWN: (*executing, LifecycleStatus.FAILED_UNKNOWN),
        LifecycleStatus.PARTIALLY_SUCCEEDED: (
            *verification,
            LifecycleStatus.PARTIALLY_SUCCEEDED,
        ),
        LifecycleStatus.STALE: (*authorized, LifecycleStatus.STALE),
        LifecycleStatus.SUPERSEDED: (
            *authorized,
            LifecycleStatus.STALE,
            LifecycleStatus.SUPERSEDED,
        ),
        LifecycleStatus.VERIFICATION_PENDING: verification,
        LifecycleStatus.VERIFICATION_UNRESOLVED: (
            *verification,
            LifecycleStatus.VERIFICATION_UNRESOLVED,
        ),
        LifecycleStatus.VERIFIED: (*verification, LifecycleStatus.VERIFIED),
    }


def _conformance_proposal(
    case: StoreConformanceCase,
    suffix: str,
) -> StoredProposal:
    proposal_reference = _derived_safe_reference(
        case.original.proposal_reference,
        purpose="proposal-probe",
        discriminator=suffix,
    )
    return case.original.model_copy(
        update={
            "proposal_reference": proposal_reference,
            "semantic_effect_reference": _derived_safe_reference(
                case.original.semantic_effect_reference,
                purpose="semantic-effect",
                discriminator=suffix,
            ),
            "protected_private_snapshot": (
                case.original.protected_private_snapshot.model_copy(
                    update={
                        "key_handle": _derived_safe_reference(
                            case.original.protected_private_snapshot.key_handle,
                            purpose="payload-key",
                            discriminator=suffix,
                        )
                    }
                )
                if case.original.protected_private_snapshot is not None
                else None
            ),
            "commitment": (
                case.original.commitment.model_copy(
                    update={
                        "key_handle": _derived_safe_reference(
                            case.original.commitment.key_handle,
                            purpose="commitment-key",
                            discriminator=suffix,
                        ),
                        "digest": _derived_safe_reference(
                            case.original.commitment.digest,
                            purpose="commitment-digest",
                            discriminator=suffix,
                        ),
                    }
                )
                if case.original.commitment is not None
                else None
            ),
            "authority_evidence": (),
            "receipts": (),
            "revision": 0,
            "erasure_pending_at": None,
            "erased_at": None,
        }
    )


def _conformance_evidence(
    case: StoreConformanceCase,
    proposal: StoredProposal,
) -> AuthorityEvidence:
    return case.evidence.model_copy(
        update={
            "proposal_instance_reference": proposal.proposal_reference,
            "semantic_effect_reference": proposal.semantic_effect_reference,
            "proposal_commitment": (
                proposal.commitment.digest if proposal.commitment is not None else "missing"
            ),
        }
    )


def _independent_proposal(original: StoredProposal, suffix: str) -> StoredProposal:
    proposal_reference = _derived_safe_reference(
        original.proposal_reference,
        purpose="proposal-independent",
        discriminator=suffix,
    )
    return original.model_copy(
        update={
            "proposal_reference": proposal_reference,
            "protected_private_snapshot": (
                original.protected_private_snapshot.model_copy(
                    update={
                        "key_handle": _derived_safe_reference(
                            original.protected_private_snapshot.key_handle,
                            purpose="payload-key",
                            discriminator=f"independent:{suffix}",
                        )
                    }
                )
                if original.protected_private_snapshot is not None
                else None
            ),
            "commitment": (
                original.commitment.model_copy(
                    update={
                        "key_handle": _derived_safe_reference(
                            original.commitment.key_handle,
                            purpose="commitment-key",
                            discriminator=f"independent:{suffix}",
                        ),
                        "digest": _derived_safe_reference(
                            original.commitment.digest,
                            purpose="commitment-digest",
                            discriminator=f"independent:{suffix}",
                        ),
                    }
                )
                if original.commitment is not None
                else None
            ),
        }
    )


def _independent_evidence(
    original: AuthorityEvidence,
    proposal: StoredProposal,
) -> AuthorityEvidence:
    return original.model_copy(
        update={
            "proposal_instance_reference": proposal.proposal_reference,
            "proposal_commitment": (
                proposal.commitment.digest if proposal.commitment is not None else "missing"
            ),
        }
    )


async def _require_update_rejected(
    case: StoreConformanceCase,
    *,
    current: StoredProposal,
    updated: StoredProposal,
    code: str,
) -> None:
    try:
        changed = await case.store.compare_and_set(
            tenant_reference=current.tenant_reference,
            proposal_reference=current.proposal_reference,
            expected_revision=current.revision,
            expected_statuses=(current.lifecycle_status,),
            updated=updated,
        )
    except StoreInvariantError:
        changed = False
    _require(not changed, code)
    _require(
        await case.store.get(current.tenant_reference, current.proposal_reference) == current,
        f"{code}:no_mutation",
    )


async def assert_providers_conform(
    *,
    commitment_provider: CommitmentProviderPort,
    protection_codec: ProtectionCodecPort,
    proposal_reference: str,
    canonical_payload: bytes,
    mutated_payload: bytes,
) -> None:
    """Check commitment binding and protected-snapshot round trips."""

    _require(canonical_payload != mutated_payload, "provider_distinct_payloads")
    commitment = await commitment_provider.create(
        proposal_reference=proposal_reference,
        canonical_payload=canonical_payload,
    )
    _require(
        await commitment_provider.verify(
            proposal_reference=proposal_reference,
            canonical_payload=canonical_payload,
            commitment=commitment,
        ),
        "commitment_original_payload",
    )
    _require(
        not await commitment_provider.verify(
            proposal_reference=proposal_reference,
            canonical_payload=mutated_payload,
            commitment=commitment,
        ),
        "commitment_rejects_mutation",
    )

    protected = await protection_codec.protect(
        proposal_reference=proposal_reference,
        canonical_payload=canonical_payload,
    )
    _require(
        canonical_payload not in protected.ciphertext.encode("utf-8"),
        "protected_payload_plaintext",
    )
    _require(
        await protection_codec.unprotect(payload=protected) == canonical_payload,
        "protected_payload_round_trip",
    )
    if isinstance(protection_codec, ProposalBoundProtectionCodec):
        await protection_codec.destroy_payload_for(
            proposal_reference=proposal_reference,
            payload=protected,
        )
        await protection_codec.destroy_payload_for(
            proposal_reference=proposal_reference,
            payload=protected,
        )
    else:
        await protection_codec.destroy_payload(payload=protected)
        await protection_codec.destroy_payload(payload=protected)
    try:
        await protection_codec.unprotect(payload=protected)
    except (KeyError, ValueError):
        pass
    else:
        raise ConformanceError("protected_payload_destroyed")

    if isinstance(commitment_provider, ProposalBoundCommitmentProvider):
        await commitment_provider.destroy_commitment_for(
            proposal_reference=proposal_reference,
            commitment=commitment,
        )
        await commitment_provider.destroy_commitment_for(
            proposal_reference=proposal_reference,
            commitment=commitment,
        )
    else:
        await commitment_provider.destroy_commitment(commitment=commitment)
        await commitment_provider.destroy_commitment(commitment=commitment)
    _require(
        not await commitment_provider.verify(
            proposal_reference=proposal_reference,
            canonical_payload=canonical_payload,
            commitment=commitment,
        ),
        "commitment_destroyed",
    )


async def assert_runtime_conforms(
    factory: Callable[[], RuntimeConformanceDriver],
) -> None:
    """Run framework-neutral happy, forged-resume, revocation, and drift scenarios."""

    valid = factory()
    prepared = await valid.prepare()
    _require(prepared.outcome is OperationOutcome.PREPARED, "runtime_prepare")
    before_unapproved = valid.executor_calls
    unapproved = await valid.execute(prepared.proposal_reference)
    _require(
        unapproved.outcome is OperationOutcome.AUTHORITY_PENDING,
        "runtime_requires_authority",
    )
    _require(valid.executor_calls == before_unapproved, "runtime_unapproved_execution")
    authorized = await valid.record_approval(prepared.proposal_reference)
    _require(authorized.outcome is OperationOutcome.AUTHORIZED, "runtime_authority")
    before_concurrent = valid.executor_calls
    concurrent = await asyncio.gather(
        valid.execute(prepared.proposal_reference),
        valid.execute(prepared.proposal_reference),
    )
    _require(
        valid.executor_calls == before_concurrent + 1,
        "runtime_concurrent_execution",
    )
    executed = next(
        (result for result in concurrent if result.outcome is not OperationOutcome.IN_PROGRESS),
        concurrent[0],
    )
    for _ in range(4):
        if executed.outcome not in {
            OperationOutcome.VERIFICATION_PENDING,
            OperationOutcome.FAILED_UNKNOWN,
        }:
            break
        executor_calls_before_verification = valid.executor_calls
        await valid.make_verification_due()
        executed = await valid.reconcile(prepared.proposal_reference)
        _require(
            valid.executor_calls == executor_calls_before_verification,
            "runtime_verification_resend",
        )
    _require(executed.outcome is OperationOutcome.VERIFIED, "runtime_verification")
    calls_after_success = valid.executor_calls
    replay = await valid.execute(prepared.proposal_reference)
    _require(replay.outcome is OperationOutcome.VERIFIED, "runtime_terminal_replay")
    _require(valid.executor_calls == calls_after_success, "runtime_duplicate_effect")

    revoked = factory()
    revoked_prepared = await revoked.prepare()
    await revoked.record_approval(revoked_prepared.proposal_reference)
    await revoked.revoke_execution_authorization()
    before_revoked = revoked.executor_calls
    blocked = await revoked.execute(revoked_prepared.proposal_reference)
    _require(blocked.outcome is OperationOutcome.BLOCKED, "runtime_live_reauthorization")
    _require(revoked.executor_calls == before_revoked, "runtime_revoked_execution")

    drifted = factory()
    drifted_prepared = await drifted.prepare()
    await drifted.record_approval(drifted_prepared.proposal_reference)
    await drifted.introduce_material_drift()
    before_drift = drifted.executor_calls
    stale = await drifted.execute(drifted_prepared.proposal_reference)
    _require(stale.outcome is OperationOutcome.STALE, "runtime_material_drift")
    _require(stale.fresh_proposal_reference is not None, "runtime_fresh_proposal")
    _require(drifted.executor_calls == before_drift, "runtime_stale_execution")


def benchmark_sync(
    operation: Callable[[], T],
    *,
    profile: PerformanceProfile,
    iterations: int | None = None,
    warmup: int = 10,
) -> BenchmarkResult:
    """Measure framework-owned synchronous work independently from external I/O."""

    count = iterations if iterations is not None else profile.min_iterations
    _validate_benchmark_counts(count=count, warmup=warmup, profile=profile)
    for _ in range(warmup):
        operation()
    samples = []
    for _ in range(count):
        started = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return _benchmark_result(samples, profile=profile)


async def benchmark_async(
    operation: Callable[[], Awaitable[T]],
    *,
    profile: PerformanceProfile,
    iterations: int | None = None,
    warmup: int = 10,
) -> BenchmarkResult:
    """Measure asynchronous orchestration supplied by an in-process test host."""

    count = iterations if iterations is not None else profile.min_iterations
    _validate_benchmark_counts(count=count, warmup=warmup, profile=profile)
    for _ in range(warmup):
        await operation()
    samples = []
    for _ in range(count):
        started = time.perf_counter_ns()
        await operation()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return _benchmark_result(samples, profile=profile)


async def benchmark_async_concurrent(
    operation: Callable[[int], Awaitable[T]],
    *,
    profile: PerformanceProfile,
    concurrency: int = 100,
    batches: int = 10,
    warmup_batches: int = 1,
) -> BenchmarkResult:
    """Measure per-operation latency while running fixed concurrent batches."""

    if concurrency <= 0 or batches <= 0 or warmup_batches < 0:
        raise ValueError("concurrency and batches must be positive; warmup must not be negative")
    count = concurrency * batches
    if count < profile.min_iterations:
        raise ValueError("concurrent samples must satisfy the performance profile minimum")

    async def timed(index: int) -> float:
        started = time.perf_counter_ns()
        await operation(index)
        return (time.perf_counter_ns() - started) / 1_000_000

    for batch in range(warmup_batches):
        offset = -(batch + 1) * concurrency
        await _gather_timings(timed, offset=offset, concurrency=concurrency)

    samples: list[float] = []
    for batch in range(batches):
        samples.extend(
            await _gather_timings(
                timed,
                offset=batch * concurrency,
                concurrency=concurrency,
            )
        )
    return _benchmark_result(samples, profile=profile)


def assert_performance_profile(result: BenchmarkResult, profile: PerformanceProfile) -> None:
    if result.profile != profile.name:
        raise ConformanceError("performance_profile_mismatch")
    if result.iterations < profile.min_iterations:
        raise ConformanceError("performance_iterations")
    if profile.max_p95_ms is not None and result.p95_ms > profile.max_p95_ms:
        raise ConformanceError("performance_p95")
    if result.p99_ms > profile.max_p99_ms:
        raise ConformanceError("performance_p99")


def _benchmark_result(
    samples: Sequence[float],
    *,
    profile: PerformanceProfile,
) -> BenchmarkResult:
    ordered = sorted(samples)
    result = BenchmarkResult(
        profile=profile.name,
        iterations=len(ordered),
        p50_ms=_percentile(ordered, 0.50),
        p95_ms=_percentile(ordered, 0.95),
        p99_ms=_percentile(ordered, 0.99),
    )
    assert_performance_profile(result, profile)
    return result


async def _gather_timings(
    operation: Callable[[int], Awaitable[float]],
    *,
    offset: int,
    concurrency: int,
) -> list[float]:
    return list(await asyncio.gather(*(operation(offset + index) for index in range(concurrency))))


def _percentile(ordered: Sequence[float], quantile: float) -> float:
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _validate_benchmark_counts(
    *,
    count: int,
    warmup: int,
    profile: PerformanceProfile,
) -> None:
    if count < profile.min_iterations:
        raise ValueError("iterations must satisfy the performance profile minimum")
    if warmup < 0:
        raise ValueError("warmup must not be negative")


def _require(condition: object, code: str) -> None:
    if not condition:
        raise ConformanceError(code)


__all__ = [
    "BenchmarkResult",
    "ConformanceError",
    "LeakageFinding",
    "PerformanceProfile",
    "RuntimeConformanceDriver",
    "StoreConformanceCase",
    "assert_action_store_conforms",
    "assert_no_sensitive_data",
    "assert_performance_profile",
    "assert_providers_conform",
    "assert_runtime_conforms",
    "benchmark_async",
    "benchmark_async_concurrent",
    "benchmark_sync",
    "find_sensitive_data",
]
