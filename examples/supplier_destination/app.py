"""Composition root for the two-service supplier-destination example."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx

from threvo_actions.authority import (
    AuthorityBinding,
    AuthorityDecision,
    AuthorityEvidence,
)
from threvo_actions.canonical import (
    KeyedCommitment,
    ProtectedPayload,
    canonicalize_v1,
    commitment_payload_v1,
)
from threvo_actions.models import (
    ActionType,
    AuthoritativeTarget,
    ConfirmingAuthority,
    ExperimentalModel,
    GovernedExecutor,
    ProposingAgent,
    RequestingPrincipal,
    SafeReference,
)
from threvo_actions.receipts import ExternalReference
from threvo_actions.registry import (
    ActionDefinition,
    AuthorityEvaluation,
    AuthorizationResult,
    DecisionContext,
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    PreparationContext,
    PreparedAction,
    ReadContext,
    ResolvedState,
    VerificationResult,
    VerificationStatus,
)
from threvo_actions.runtime import ActionOperationResult, ActionRuntime
from threvo_actions.stores.memory import MemoryActionStore

from .domain import (
    BankDestination,
    DestinationChangeCommand,
    DestinationChangePreview,
    DestinationChangeResult,
    DestinationChangeSnapshot,
    EffectKind,
    EffectQueryStatus,
    ExtractionCandidate,
    PaymentCommand,
    PaymentPreview,
    PaymentResult,
    PaymentSnapshot,
    ReceiverMutationStatus,
    ReceiverState,
)
from .fake_supplier_master import (
    SEEDED_IBAN,
    SEEDED_INTERNAL_SUPPLIER_ID,
    FakeSupplierMaster,
    SupplierRecord,
)
from .initiator_service import (
    DestinationAuthoritySubmission,
    InitiatorAuthenticator,
    InitiatorIdentity,
    PaymentAuthoritySubmission,
    create_initiator_app,
)
from .receiver_service import ReceiverAuthenticator, ReceiverIdentity, create_receiver_app
from .transport import (
    DestinationMutationEnvelope,
    EffectQueryEnvelope,
    PaymentMutationEnvelope,
    ReceiverRejectedError,
    SupplierMasterTransport,
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from pydantic import JsonValue

DESTINATION_ACTION = ActionType(
    namespace="example.supplier_master",
    name="change_destination",
    version=1,
)
PAYMENT_ACTION = ActionType(
    namespace="example.supplier_master",
    name="release_payment",
    version=1,
)
TENANT_REFERENCE = "tenant:example"
SUPPLIER_REFERENCE = "supplier:acme"
RECEIVER_AUDIENCE = "service:supplier-master-receiver"
INITIATOR_AUDIENCE = "service:accountable-action-initiator"
PAYABLES_APPROVER = "principal:payables-approver"
DESTINATION_VERIFIER = "principal:destination-verifier"
PAYMENT_RELEASER = "principal:payment-releaser"
EXTRACTION_REFERENCE = "extraction:supplier-acme-destination"
RECEIVER_CREDENTIAL = "receiver-local-credential"
REQUESTER_CREDENTIAL = "initiator-requester-credential"
APPROVER_CREDENTIAL = "initiator-approver-credential"
VERIFIER_CREDENTIAL = "initiator-verifier-credential"
PAYMENT_CREDENTIAL = "initiator-payment-credential"
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
REQUESTER_IDENTITY = InitiatorIdentity(
    tenant_reference=TENANT_REFERENCE,
    principal_reference="principal:requester",
)
APPROVER_IDENTITY = InitiatorIdentity(
    tenant_reference=TENANT_REFERENCE,
    principal_reference=PAYABLES_APPROVER,
)
VERIFIER_IDENTITY = InitiatorIdentity(
    tenant_reference=TENANT_REFERENCE,
    principal_reference=DESTINATION_VERIFIER,
)
PAYMENT_IDENTITY = InitiatorIdentity(
    tenant_reference=TENANT_REFERENCE,
    principal_reference=PAYMENT_RELEASER,
)


class ExampleClock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current


class SequenceIdentifiers:
    def __init__(self) -> None:
        self._value = 0

    def new(self, prefix: str) -> str:
        self._value += 1
        return f"{prefix}:{self._value}"


class VersionedExtractionRegistry:
    """Initiator-owned private registry for untrusted extraction candidates."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ExtractionCandidate] = {}
        self._claims: dict[tuple[str, str], tuple[str, int, BankDestination, str]] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        *,
        tenant_reference: str,
        extraction_reference: str,
        supplier_reference: str,
        destination: BankDestination,
    ) -> ExtractionCandidate:
        key = (tenant_reference, extraction_reference)
        async with self._lock:
            if key in self._claims:
                raise RuntimeError("extraction is claimed for execution")
            existing = self._records.get(key)
            version = 1 if existing is None else existing.extraction_version + 1
            candidate = ExtractionCandidate(
                tenant_reference=tenant_reference,
                extraction_reference=extraction_reference,
                extraction_version=version,
                supplier_reference=supplier_reference,
                destination=destination,
            )
            self._records[key] = candidate
            return candidate

    async def resolve(
        self, *, tenant_reference: str, extraction_reference: str
    ) -> ExtractionCandidate:
        async with self._lock:
            candidate = self._records.get((tenant_reference, extraction_reference))
            if candidate is None:
                raise LookupError("extraction not found")
            return candidate.model_copy(deep=True)

    async def claim(
        self,
        *,
        tenant_reference: str,
        extraction_reference: str,
        extraction_version: int,
        supplier_reference: str,
        destination: BankDestination,
        semantic_effect_reference: str,
    ) -> bool:
        key = (tenant_reference, extraction_reference)
        requested = (
            semantic_effect_reference,
            extraction_version,
            destination,
            supplier_reference,
        )
        async with self._lock:
            existing_claim = self._claims.get(key)
            if existing_claim is not None:
                return existing_claim == requested
            candidate = self._records.get(key)
            if candidate is None:
                return False
            matches = (
                candidate.extraction_version == extraction_version
                and candidate.supplier_reference == supplier_reference
                and candidate.destination == destination
            )
            if matches:
                self._claims[key] = requested
            return matches

    async def release_claim(
        self,
        *,
        tenant_reference: str,
        extraction_reference: str,
        semantic_effect_reference: str,
    ) -> None:
        key = (tenant_reference, extraction_reference)
        async with self._lock:
            existing = self._claims.get(key)
            if existing is not None and existing[0] == semantic_effect_reference:
                self._claims.pop(key, None)


class OOBVerificationRecord(ExperimentalModel):
    verification_reference: SafeReference
    tenant_reference: SafeReference
    proposal_reference: SafeReference
    principal_reference: SafeReference
    extraction_reference: SafeReference
    extraction_version: int
    proposal_commitment: SafeReference


class OOBVerificationRegistry:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], OOBVerificationRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: OOBVerificationRecord) -> None:
        async with self._lock:
            self._records[(record.tenant_reference, record.verification_reference)] = record

    async def get(
        self, *, tenant_reference: str, verification_reference: str
    ) -> OOBVerificationRecord:
        async with self._lock:
            record = self._records.get((tenant_reference, verification_reference))
            if record is None:
                raise LookupError("verification not found")
            return record.model_copy(deep=True)


class VaultedExampleSecrets:
    """Opaque store payloads with proposal-scoped commitments for this local example."""

    def __init__(self) -> None:
        self._material = b"supplier-destination-local-example"
        self._payloads: dict[str, bytes] = {}
        self._destroyed_commitments: set[str] = set()

    async def create(self, *, proposal_reference: str, canonical_payload: bytes) -> KeyedCommitment:
        digest = hmac.new(self._material, canonical_payload, hashlib.sha256).hexdigest()
        return KeyedCommitment(
            algorithm="hmac-sha256",
            key_handle=f"commitment:{proposal_reference}",
            key_version="local-v1",
            digest=digest,
        )

    async def verify(
        self,
        *,
        proposal_reference: str,
        canonical_payload: bytes,
        commitment: KeyedCommitment,
    ) -> bool:
        if commitment.key_handle != f"commitment:{proposal_reference}":
            return False
        if commitment.key_handle in self._destroyed_commitments:
            return False
        expected = hmac.new(self._material, canonical_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, commitment.digest)

    async def destroy_commitment(self, *, commitment: KeyedCommitment) -> None:
        self._destroyed_commitments.add(commitment.key_handle)

    async def protect(
        self, *, proposal_reference: str, canonical_payload: bytes
    ) -> ProtectedPayload:
        handle = f"payload:{proposal_reference}"
        self._payloads[handle] = canonical_payload
        opaque = base64.b64encode(hashlib.sha256(canonical_payload).digest()).decode()
        return ProtectedPayload(
            codec="example-private-vault",
            key_handle=handle,
            key_version="local-v1",
            ciphertext=opaque,
        )

    async def unprotect(self, *, payload: ProtectedPayload) -> bytes:
        return self._payloads[payload.key_handle]

    async def destroy_payload(self, *, payload: ProtectedPayload) -> None:
        self._payloads.pop(payload.key_handle, None)


def _masked_destination(iban: str) -> str:
    return f"••••{iban[-4:]}"


def _destination_effect(
    supplier_reference: str,
    extraction_reference: str,
    extraction_version: int,
) -> str:
    binding = _request_binding(
        "destination-change-effect",
        {
            "supplier_reference": supplier_reference,
            "extraction_reference": extraction_reference,
            "extraction_version": extraction_version,
        },
    )
    return f"destination-change:{binding}"


def _snapshot_destination_effect(snapshot: DestinationChangeSnapshot) -> str:
    return _destination_effect(
        snapshot.supplier_reference,
        snapshot.extraction_reference,
        snapshot.extraction_version,
    )


def _request_binding(kind: str, payload: dict[str, JsonValue]) -> str:
    canonical = canonicalize_v1({"kind": kind, "payload": payload})
    return hmac.new(
        b"supplier-destination-request-binding-example",
        canonical,
        hashlib.sha256,
    ).hexdigest()


def _payment_effect(payment_reference: str) -> str:
    binding = _request_binding(
        "payment-release-effect",
        {"payment_reference": payment_reference},
    )
    return f"payment-release:{binding}"


def _destination_request_binding(snapshot: DestinationChangeSnapshot) -> str:
    return _request_binding(
        "destination_change",
        {
            "supplier_reference": snapshot.supplier_reference,
            "supplier_version": snapshot.supplier_version,
            "previous_verified_destination_version": (
                snapshot.previous_verified_destination_version
            ),
            "extraction_reference": snapshot.extraction_reference,
            "extraction_version": snapshot.extraction_version,
            "destination": snapshot.extracted_destination.model_dump(mode="json"),
        },
    )


def _payment_request_binding(snapshot: PaymentSnapshot) -> str:
    return _request_binding(
        "payment_release",
        {
            "payment_reference": snapshot.payment_reference,
            "supplier_reference": snapshot.supplier_reference,
            "supplier_version": snapshot.supplier_version,
            "amount": snapshot.amount.model_dump(mode="json"),
            "verified_destination_version": snapshot.verified_destination_version,
        },
    )


class DestinationPorts:
    def __init__(
        self,
        transport: SupplierMasterTransport,
        identifiers: SequenceIdentifiers,
        extractions: VersionedExtractionRegistry,
    ) -> None:
        self._transport = transport
        self._identifiers = identifiers
        self._extractions = extractions
        self._expected_bindings: dict[str, tuple[str, int, str, str]] = {}

    async def prepare(
        self,
        command: DestinationChangeCommand,
        *,
        context: PreparationContext,
    ) -> PreparedAction[DestinationChangeSnapshot, DestinationChangePreview]:
        candidate = await self._extractions.resolve(
            tenant_reference=context.tenant_reference,
            extraction_reference=command.extraction_reference,
        )
        if candidate.supplier_reference != command.supplier_reference:
            raise LookupError("extraction not found")
        state = await self._transport.state(
            supplier_reference=command.supplier_reference,
            message_reference=self._identifiers.new("message"),
        )
        snapshot = DestinationChangeSnapshot(
            supplier_reference=command.supplier_reference,
            supplier_version=state.supplier_version,
            previous_verified_destination_version=state.verified_destination_version,
            extraction_reference=candidate.extraction_reference,
            extraction_version=candidate.extraction_version,
            extracted_destination=candidate.destination,
        )
        return PreparedAction(
            private_snapshot=snapshot,
            display_preview=DestinationChangePreview(
                summary="Change supplier payment destination after independent verification",
                masked_destination=_masked_destination(candidate.destination.iban),
            ),
            semantic_effect_reference=_destination_effect(
                command.supplier_reference,
                candidate.extraction_reference,
                candidate.extraction_version,
            ),
        )

    async def can_prepare(
        self,
        command: DestinationChangeCommand,
        *,
        context: PreparationContext,
    ) -> AuthorizationResult:
        del command
        allowed = (
            context.tenant_reference == TENANT_REFERENCE
            and context.requesting_principal.reference == REQUESTER_IDENTITY.principal_reference
        )
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "destination_prepare_denied",
        )

    async def can_decide(
        self,
        evidence: AuthorityEvidence,
        *,
        context: DecisionContext,
    ) -> AuthorizationResult:
        allowed = (
            context.tenant_reference == TENANT_REFERENCE
            and evidence.authority == context.authority
            and context.authority.reference
            in {
                PAYABLES_APPROVER,
                DESTINATION_VERIFIER,
            }
        )
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "authority_role_denied",
        )

    async def can_execute(
        self,
        snapshot: DestinationChangeSnapshot,
        *,
        context: ExecutionContext,
    ) -> AuthorizationResult:
        del snapshot
        authority_references = {authority.reference for authority in context.authorities}
        allowed = (
            context.tenant_reference == TENANT_REFERENCE
            and context.requesting_principal.reference == REQUESTER_IDENTITY.principal_reference
            and {PAYABLES_APPROVER, DESTINATION_VERIFIER}.issubset(authority_references)
        )
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "destination_execution_denied",
        )

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference
        return context.tenant_reference == TENANT_REFERENCE

    async def evaluate(
        self,
        *,
        binding: AuthorityBinding,
        evidence: tuple[AuthorityEvidence, ...],
    ) -> AuthorityEvaluation:
        del binding
        approving = {
            item.authority.reference
            for item in evidence
            if item.decision is AuthorityDecision.APPROVE
        }
        satisfied = {PAYABLES_APPROVER, DESTINATION_VERIFIER}.issubset(approving)
        return AuthorityEvaluation(
            satisfied=satisfied,
            reason_code=None if satisfied else "distinct_authorities_required",
        )

    async def resolve(
        self,
        snapshot: DestinationChangeSnapshot,
        *,
        context: ExecutionContext,
    ) -> ResolvedState[DestinationChangeSnapshot, DestinationChangePreview]:
        candidate = await self._extractions.resolve(
            tenant_reference=context.tenant_reference,
            extraction_reference=snapshot.extraction_reference,
        )
        state = await self._transport.state(
            supplier_reference=snapshot.supplier_reference,
            message_reference=self._identifiers.new("message"),
        )
        current = snapshot.model_copy(
            update={
                "supplier_version": state.supplier_version,
                "previous_verified_destination_version": (state.verified_destination_version),
                "extraction_version": candidate.extraction_version,
                "extracted_destination": candidate.destination,
            }
        )
        extraction_drifted = (
            candidate.supplier_reference != snapshot.supplier_reference
            or candidate.extraction_version != snapshot.extraction_version
            or candidate.destination != snapshot.extracted_destination
        )
        drifted = state.supplier_version != snapshot.supplier_version or extraction_drifted
        replacement = None
        if drifted and candidate.supplier_reference == snapshot.supplier_reference:
            replacement = PreparedAction(
                private_snapshot=current,
                display_preview=DestinationChangePreview(
                    summary="Change supplier payment destination after independent verification",
                    masked_destination=_masked_destination(candidate.destination.iban),
                ),
                semantic_effect_reference=_snapshot_destination_effect(current),
            )
        return ResolvedState(
            current_snapshot=current,
            execution_precondition=f"supplier-version:{state.supplier_version}",
            materially_drifted=drifted,
            replacement=replacement,
        )

    async def execute(
        self,
        snapshot: DestinationChangeSnapshot,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[DestinationChangeResult]:
        del execution_precondition
        request_binding = _destination_request_binding(snapshot)
        expected = (
            snapshot.supplier_reference,
            snapshot.previous_verified_destination_version + 1,
            request_binding,
            snapshot.extraction_reference,
        )
        existing = self._expected_bindings.setdefault(
            context.semantic_effect_reference,
            expected,
        )
        if existing != expected:
            return ExecutionResult[DestinationChangeResult](
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code="target_binding_conflict",
            )
        claimed = await self._extractions.claim(
            tenant_reference=context.tenant_reference,
            extraction_reference=snapshot.extraction_reference,
            extraction_version=snapshot.extraction_version,
            supplier_reference=snapshot.supplier_reference,
            destination=snapshot.extracted_destination,
            semantic_effect_reference=context.semantic_effect_reference,
        )
        if not claimed:
            return ExecutionResult[DestinationChangeResult](
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code="extraction_version_changed",
            )
        request = DestinationMutationEnvelope(
            message_reference=self._identifiers.new("message"),
            audience=RECEIVER_AUDIENCE,
            semantic_effect_reference=context.semantic_effect_reference,
            request_binding=request_binding,
            supplier_reference=snapshot.supplier_reference,
            expected_supplier_version=snapshot.supplier_version,
            destination=snapshot.extracted_destination,
        )
        try:
            response = await self._transport.submit_destination(request)
        except (httpx.TimeoutException, ReceiverRejectedError):
            return ExecutionResult[DestinationChangeResult](
                status=ExecutionStatus.FAILED_UNKNOWN,
                reason_code="receiver_outcome_unknown",
            )
        if response.status is ReceiverMutationStatus.PRECONDITION_FAILED:
            await self._release_extraction_claim(context, snapshot.extraction_reference)
            return ExecutionResult[DestinationChangeResult](
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code=response.reason_code or "atomic_precondition_failed",
            )
        if response.supplier_reference is None or response.verified_destination_version is None:
            return ExecutionResult[DestinationChangeResult](
                status=ExecutionStatus.FAILED_UNKNOWN,
                reason_code="receiver_result_invalid",
            )
        if (
            response.supplier_reference != expected[0]
            or response.verified_destination_version != expected[1]
        ):
            return ExecutionResult[DestinationChangeResult](
                status=ExecutionStatus.FAILED_UNKNOWN,
                reason_code="receiver_binding_mismatch",
            )
        return ExecutionResult[DestinationChangeResult](
            status=ExecutionStatus.ACCEPTED,
            result=DestinationChangeResult(
                supplier_reference=response.supplier_reference,
                verified_destination_version=response.verified_destination_version,
            ),
            external_reference=ExternalReference(
                system="supplier-master",
                reference=context.semantic_effect_reference,
            ),
        )

    async def verify(
        self, *, context: ExecutionContext
    ) -> VerificationResult[DestinationChangeResult]:
        query = await self._transport.query_effect(
            EffectQueryEnvelope(
                message_reference=self._identifiers.new("message"),
                audience=RECEIVER_AUDIENCE,
                effect_kind=EffectKind.DESTINATION_CHANGE.value,
                semantic_effect_reference=context.semantic_effect_reference,
            )
        )
        if query.status is EffectQueryStatus.ABSENT or query.destination_result is None:
            return VerificationResult[DestinationChangeResult](
                status=VerificationStatus.PROVISIONAL_ABSENCE,
                reason_code="effect_not_yet_visible",
            )
        result = query.destination_result
        expected = self._expected_bindings.get(context.semantic_effect_reference)
        if result.supplier_reference is None or result.verified_destination_version is None:
            return VerificationResult[DestinationChangeResult](
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="receiver_result_invalid",
            )
        if (
            expected is None
            or query.request_binding != expected[2]
            or result.supplier_reference != expected[0]
            or result.verified_destination_version != expected[1]
        ):
            return VerificationResult[DestinationChangeResult](
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="receiver_binding_mismatch",
            )
        await self._release_extraction_claim(
            context,
            extraction_reference=expected[3],
        )
        return VerificationResult[DestinationChangeResult](
            status=VerificationStatus.VERIFIED_COMPLETION,
            result=DestinationChangeResult(
                supplier_reference=result.supplier_reference,
                verified_destination_version=result.verified_destination_version,
            ),
            external_reference=ExternalReference(
                system="supplier-master",
                reference=context.semantic_effect_reference,
            ),
        )

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference, context
        return False

    async def _release_extraction_claim(
        self,
        context: ExecutionContext,
        extraction_reference: str,
    ) -> None:
        await self._extractions.release_claim(
            tenant_reference=context.tenant_reference,
            extraction_reference=extraction_reference,
            semantic_effect_reference=context.semantic_effect_reference,
        )


class PaymentPorts:
    def __init__(
        self, transport: SupplierMasterTransport, identifiers: SequenceIdentifiers
    ) -> None:
        self._transport = transport
        self._identifiers = identifiers
        self._expected_bindings: dict[str, tuple[str, int, str]] = {}

    async def prepare(
        self, command: PaymentCommand, *, context: PreparationContext
    ) -> PreparedAction[PaymentSnapshot, PaymentPreview]:
        state = await self._state(command, context.tenant_reference)
        return PreparedAction(
            private_snapshot=PaymentSnapshot(
                payment_reference=command.payment_reference,
                supplier_reference=command.supplier_reference,
                supplier_version=state.supplier_version,
                amount=command.amount,
                verified_destination_version=command.verified_destination_version,
            ),
            display_preview=PaymentPreview(
                summary=(
                    f"Release {command.amount.currency} {command.amount.amount} supplier payment"
                ),
                destination_binding=(f"verified-version:{command.verified_destination_version}"),
            ),
            semantic_effect_reference=_payment_effect(command.payment_reference),
        )

    async def can_prepare(
        self, command: PaymentCommand, *, context: PreparationContext
    ) -> AuthorizationResult:
        if (
            context.tenant_reference != TENANT_REFERENCE
            or context.requesting_principal.reference != REQUESTER_IDENTITY.principal_reference
        ):
            return AuthorizationResult(
                allowed=False,
                reason_code="payment_prepare_denied",
            )
        state = await self._state(command, context.tenant_reference)
        allowed = state.verified_destination_version == command.verified_destination_version
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "destination_version_not_verified",
        )

    async def _state(self, command: PaymentCommand, tenant_reference: str) -> ReceiverState:
        del tenant_reference
        return await self._transport.state(
            supplier_reference=command.supplier_reference,
            message_reference=self._identifiers.new("message"),
        )

    async def can_decide(
        self,
        evidence: AuthorityEvidence,
        *,
        context: DecisionContext,
    ) -> AuthorizationResult:
        allowed = (
            context.tenant_reference == TENANT_REFERENCE
            and evidence.authority == context.authority
            and context.authority.reference == PAYMENT_RELEASER
        )
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "payment_authority_denied",
        )

    async def can_execute(
        self, snapshot: PaymentSnapshot, *, context: ExecutionContext
    ) -> AuthorizationResult:
        del snapshot
        allowed = (
            context.tenant_reference == TENANT_REFERENCE
            and context.requesting_principal.reference == REQUESTER_IDENTITY.principal_reference
            and any(authority.reference == PAYMENT_RELEASER for authority in context.authorities)
        )
        return AuthorizationResult(
            allowed=allowed,
            reason_code=None if allowed else "payment_execution_denied",
        )

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference
        return context.tenant_reference == TENANT_REFERENCE

    async def evaluate(
        self,
        *,
        binding: AuthorityBinding,
        evidence: tuple[AuthorityEvidence, ...],
    ) -> AuthorityEvaluation:
        del binding
        satisfied = any(
            item.decision is AuthorityDecision.APPROVE
            and item.authority.reference == PAYMENT_RELEASER
            for item in evidence
        )
        return AuthorityEvaluation(
            satisfied=satisfied,
            reason_code=None if satisfied else "payment_authority_required",
        )

    async def resolve(
        self, snapshot: PaymentSnapshot, *, context: ExecutionContext
    ) -> ResolvedState[PaymentSnapshot, PaymentPreview]:
        state = await self._transport.state(
            supplier_reference=snapshot.supplier_reference,
            message_reference=self._identifiers.new("message"),
        )
        current = snapshot.model_copy(update={"supplier_version": state.supplier_version})
        destination_still_bound = (
            state.verified_destination_version == snapshot.verified_destination_version
        )
        drifted = state.supplier_version != snapshot.supplier_version or not destination_still_bound
        replacement = None
        if drifted and destination_still_bound:
            replacement = PreparedAction(
                private_snapshot=current,
                display_preview=PaymentPreview(
                    summary=(
                        f"Release {snapshot.amount.currency} {snapshot.amount.amount} "
                        "supplier payment"
                    ),
                    destination_binding=(
                        f"verified-version:{snapshot.verified_destination_version}"
                    ),
                ),
                semantic_effect_reference=_payment_effect(snapshot.payment_reference),
            )
        return ResolvedState(
            current_snapshot=current,
            execution_precondition=f"supplier-version:{state.supplier_version}",
            materially_drifted=drifted,
            replacement=replacement,
        )

    async def execute(
        self,
        snapshot: PaymentSnapshot,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[PaymentResult]:
        del execution_precondition
        request_binding = _payment_request_binding(snapshot)
        expected = (
            snapshot.supplier_reference,
            snapshot.verified_destination_version,
            request_binding,
        )
        existing = self._expected_bindings.setdefault(
            context.semantic_effect_reference,
            expected,
        )
        if existing != expected:
            return ExecutionResult[PaymentResult](
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code="target_binding_conflict",
            )
        try:
            response = await self._transport.submit_payment(
                PaymentMutationEnvelope(
                    message_reference=self._identifiers.new("message"),
                    audience=RECEIVER_AUDIENCE,
                    semantic_effect_reference=context.semantic_effect_reference,
                    request_binding=request_binding,
                    supplier_reference=snapshot.supplier_reference,
                    expected_supplier_version=snapshot.supplier_version,
                    verified_destination_version=snapshot.verified_destination_version,
                    amount=snapshot.amount,
                )
            )
        except (httpx.TimeoutException, ReceiverRejectedError):
            return ExecutionResult[PaymentResult](
                status=ExecutionStatus.FAILED_UNKNOWN,
                reason_code="receiver_outcome_unknown",
            )
        if response.status is ReceiverMutationStatus.PRECONDITION_FAILED:
            return ExecutionResult[PaymentResult](
                status=ExecutionStatus.FAILED_KNOWN,
                reason_code=response.reason_code or "atomic_precondition_failed",
            )
        if (
            response.supplier_reference is None
            or response.payment_reference is None
            or response.verified_destination_version is None
        ):
            return ExecutionResult[PaymentResult](
                status=ExecutionStatus.FAILED_UNKNOWN,
                reason_code="receiver_result_invalid",
            )
        if (
            response.supplier_reference != expected[0]
            or response.verified_destination_version != expected[1]
        ):
            return ExecutionResult[PaymentResult](
                status=ExecutionStatus.FAILED_UNKNOWN,
                reason_code="receiver_binding_mismatch",
            )
        return ExecutionResult[PaymentResult](
            status=ExecutionStatus.ACCEPTED,
            result=PaymentResult(
                supplier_reference=response.supplier_reference,
                payment_reference=response.payment_reference,
                verified_destination_version=response.verified_destination_version,
            ),
        )

    async def verify(self, *, context: ExecutionContext) -> VerificationResult[PaymentResult]:
        query = await self._transport.query_effect(
            EffectQueryEnvelope(
                message_reference=self._identifiers.new("message"),
                audience=RECEIVER_AUDIENCE,
                effect_kind=EffectKind.PAYMENT_RELEASE.value,
                semantic_effect_reference=context.semantic_effect_reference,
            )
        )
        if query.status is EffectQueryStatus.ABSENT or query.payment_result is None:
            return VerificationResult[PaymentResult](
                status=VerificationStatus.PROVISIONAL_ABSENCE,
                reason_code="effect_not_yet_visible",
            )
        result = query.payment_result
        expected = self._expected_bindings.get(context.semantic_effect_reference)
        if (
            result.supplier_reference is None
            or result.payment_reference is None
            or result.verified_destination_version is None
        ):
            return VerificationResult[PaymentResult](
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="receiver_result_invalid",
            )
        if (
            expected is None
            or query.request_binding != expected[2]
            or result.supplier_reference != expected[0]
            or result.verified_destination_version != expected[1]
        ):
            return VerificationResult[PaymentResult](
                status=VerificationStatus.TARGET_UNAVAILABLE,
                reason_code="receiver_binding_mismatch",
            )
        return VerificationResult[PaymentResult](
            status=VerificationStatus.VERIFIED_COMPLETION,
            result=PaymentResult(
                supplier_reference=result.supplier_reference,
                payment_reference=result.payment_reference,
                verified_destination_version=result.verified_destination_version,
            ),
        )

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference, context
        return False


class SupplierDestinationApplication:
    def __init__(
        self,
        *,
        runtime: ActionRuntime,
        store: MemoryActionStore,
        clock: ExampleClock,
        secrets: VaultedExampleSecrets,
        identifiers: SequenceIdentifiers,
        extractions: VersionedExtractionRegistry,
        oob_verifications: OOBVerificationRegistry,
        destination_definition: ActionDefinition[
            DestinationChangeCommand,
            DestinationChangeSnapshot,
            DestinationChangePreview,
            DestinationChangeResult,
        ],
        payment_definition: ActionDefinition[
            PaymentCommand,
            PaymentSnapshot,
            PaymentPreview,
            PaymentResult,
        ],
    ) -> None:
        self.runtime = runtime
        self.store = store
        self.clock = clock
        self.secrets = secrets
        self.identifiers = identifiers
        self.extractions = extractions
        self.oob_verifications = oob_verifications
        self.destination_definition = destination_definition
        self.payment_definition = payment_definition

    async def prepare_destination(
        self,
        command: DestinationChangeCommand,
        identity: InitiatorIdentity,
    ) -> ActionOperationResult:
        return await self.runtime.prepare(
            self.destination_definition,
            tenant_reference=identity.tenant_reference,
            command=command,
            requesting_principal=RequestingPrincipal(reference=identity.principal_reference),
            proposing_agent=ProposingAgent(reference="agent:payables-assistant"),
        )

    async def register_oob_verification(
        self,
        proposal_reference: str,
        identity: InitiatorIdentity,
        *,
        observed_destination: BankDestination,
    ) -> str:
        if identity.principal_reference not in {PAYABLES_APPROVER, DESTINATION_VERIFIER}:
            raise PermissionError("authority role denied")
        snapshot, commitment = await self._destination_snapshot(
            identity.tenant_reference, proposal_reference
        )
        candidate = await self.extractions.resolve(
            tenant_reference=identity.tenant_reference,
            extraction_reference=snapshot.extraction_reference,
        )
        candidate_matches = (
            candidate.extraction_version == snapshot.extraction_version
            and candidate.supplier_reference == snapshot.supplier_reference
            and candidate.destination == snapshot.extracted_destination
        )
        if not candidate_matches or observed_destination != snapshot.extracted_destination:
            raise ValueError("out-of-band destination did not match the committed proposal")
        reference = self.identifiers.new("verification")
        await self.oob_verifications.put(
            OOBVerificationRecord(
                verification_reference=reference,
                tenant_reference=identity.tenant_reference,
                proposal_reference=proposal_reference,
                principal_reference=identity.principal_reference,
                extraction_reference=snapshot.extraction_reference,
                extraction_version=snapshot.extraction_version,
                proposal_commitment=commitment,
            )
        )
        return reference

    async def record_destination_authority(
        self,
        proposal_reference: str,
        submission: DestinationAuthoritySubmission,
        identity: InitiatorIdentity,
    ) -> ActionOperationResult:
        verification = await self.oob_verifications.get(
            tenant_reference=identity.tenant_reference,
            verification_reference=submission.verification_reference,
        )
        snapshot, commitment = await self._destination_snapshot(
            identity.tenant_reference, proposal_reference
        )
        candidate = await self.extractions.resolve(
            tenant_reference=identity.tenant_reference,
            extraction_reference=snapshot.extraction_reference,
        )
        binding_matches = (
            verification.proposal_reference == proposal_reference
            and verification.principal_reference == identity.principal_reference
            and verification.extraction_reference == snapshot.extraction_reference
            and verification.extraction_version == snapshot.extraction_version
            and verification.proposal_commitment == commitment
            and candidate.supplier_reference == snapshot.supplier_reference
            and candidate.extraction_version == snapshot.extraction_version
            and candidate.destination == snapshot.extracted_destination
        )
        if not binding_matches:
            raise LookupError("verification not found")
        evidence = await self._evidence(
            tenant_reference=identity.tenant_reference,
            action_type=self.destination_definition.action_type,
            authority_audience=self.destination_definition.authority_audience,
            proposal_reference=proposal_reference,
            principal_reference=identity.principal_reference,
            channel_assurance="out_of_band_exact_match",
        )
        return await self.runtime.record_authority(
            self.destination_definition,
            evidence=evidence,
            authenticated_authority=evidence.authority,
        )

    async def execute_destination(
        self, proposal_reference: str, identity: InitiatorIdentity
    ) -> ActionOperationResult:
        await self._authorize_trigger(
            proposal_reference,
            identity,
            action_type=self.destination_definition.action_type,
        )
        return await self.runtime.execute(
            self.destination_definition,
            tenant_reference=identity.tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def reconcile_destination(
        self, proposal_reference: str, identity: InitiatorIdentity
    ) -> ActionOperationResult:
        await self._authorize_trigger(
            proposal_reference,
            identity,
            action_type=self.destination_definition.action_type,
        )
        return await self.runtime.reconcile(
            self.destination_definition,
            tenant_reference=identity.tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def prepare_payment(
        self, command: PaymentCommand, identity: InitiatorIdentity
    ) -> ActionOperationResult:
        return await self.runtime.prepare(
            self.payment_definition,
            tenant_reference=identity.tenant_reference,
            command=command,
            requesting_principal=RequestingPrincipal(reference=identity.principal_reference),
            proposing_agent=ProposingAgent(reference="agent:payables-assistant"),
        )

    async def record_payment_authority(
        self,
        proposal_reference: str,
        submission: PaymentAuthoritySubmission,
        identity: InitiatorIdentity,
    ) -> ActionOperationResult:
        del submission
        evidence = await self._evidence(
            tenant_reference=identity.tenant_reference,
            action_type=self.payment_definition.action_type,
            authority_audience=self.payment_definition.authority_audience,
            proposal_reference=proposal_reference,
            principal_reference=identity.principal_reference,
            channel_assurance="authenticated_session",
        )
        return await self.runtime.record_authority(
            self.payment_definition,
            evidence=evidence,
            authenticated_authority=evidence.authority,
        )

    async def execute_payment(
        self, proposal_reference: str, identity: InitiatorIdentity
    ) -> ActionOperationResult:
        await self._authorize_trigger(
            proposal_reference,
            identity,
            action_type=self.payment_definition.action_type,
        )
        return await self.runtime.execute(
            self.payment_definition,
            tenant_reference=identity.tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def reconcile_payment(
        self, proposal_reference: str, identity: InitiatorIdentity
    ) -> ActionOperationResult:
        await self._authorize_trigger(
            proposal_reference,
            identity,
            action_type=self.payment_definition.action_type,
        )
        return await self.runtime.reconcile(
            self.payment_definition,
            tenant_reference=identity.tenant_reference,
            proposal_reference=proposal_reference,
        )

    async def _destination_snapshot(
        self, tenant_reference: str, proposal_reference: str
    ) -> tuple[DestinationChangeSnapshot, str]:
        record = await self.store.get(tenant_reference, proposal_reference)
        if (
            record is None
            or record.action_type != DESTINATION_ACTION
            or record.protected_private_snapshot is None
            or record.commitment is None
        ):
            raise LookupError("proposal not found")
        canonical = await self.secrets.unprotect(payload=record.protected_private_snapshot)
        commitment_input = commitment_payload_v1(
            proposal_reference=proposal_reference,
            canonical_payload=canonical,
        )
        if not await self.secrets.verify(
            proposal_reference=proposal_reference,
            canonical_payload=commitment_input,
            commitment=record.commitment,
        ):
            raise ValueError("proposal commitment could not be verified")
        snapshot = DestinationChangeSnapshot.model_validate_json(canonical)
        return snapshot, record.commitment.digest

    async def _authorize_trigger(
        self,
        proposal_reference: str,
        identity: InitiatorIdentity,
        *,
        action_type: ActionType,
    ) -> None:
        record = await self.store.get(identity.tenant_reference, proposal_reference)
        if (
            record is None
            or record.action_type != action_type
            or record.requesting_principal is None
            or record.requesting_principal.reference != identity.principal_reference
        ):
            raise PermissionError("action trigger denied")

    async def _evidence(
        self,
        *,
        tenant_reference: str,
        action_type: ActionType,
        authority_audience: str,
        proposal_reference: str,
        principal_reference: str,
        channel_assurance: str,
    ) -> AuthorityEvidence:
        record = await self.store.get(tenant_reference, proposal_reference)
        if record is None or record.action_type != action_type or record.commitment is None:
            raise LookupError("proposal not found")
        return AuthorityEvidence(
            tenant_reference=tenant_reference,
            action_type=action_type,
            proposal_instance_reference=proposal_reference,
            semantic_effect_reference=record.semantic_effect_reference,
            authority=ConfirmingAuthority(reference=principal_reference),
            audience=(authority_audience,),
            decision=AuthorityDecision.APPROVE,
            proposal_commitment=record.commitment.digest,
            channel_assurance=channel_assurance,
            issued_at=self.clock.now() - timedelta(seconds=1),
            expires_at=self.clock.now() + timedelta(minutes=10),
        )


@dataclass(frozen=True)
class SupplierDestinationExample:
    application: SupplierDestinationApplication
    master: FakeSupplierMaster
    transport: SupplierMasterTransport
    extractions: VersionedExtractionRegistry
    receiver_app: FastAPI
    initiator_app: FastAPI


async def build_example() -> SupplierDestinationExample:
    master = FakeSupplierMaster()
    await master.seed(
        SupplierRecord(
            tenant_reference=TENANT_REFERENCE,
            internal_supplier_id=SEEDED_INTERNAL_SUPPLIER_ID,
            supplier_reference=SUPPLIER_REFERENCE,
            supplier_version=1,
            destination=BankDestination(
                iban="DE89370400440532013000",
                bic="COBADEFFXXX",
                account_holder="Acme Components",
            ),
            destination_version=1,
            verified_destination_version=1,
        )
    )
    extractions = VersionedExtractionRegistry()
    await extractions.register(
        tenant_reference=TENANT_REFERENCE,
        extraction_reference=EXTRACTION_REFERENCE,
        supplier_reference=SUPPLIER_REFERENCE,
        destination=BankDestination(
            iban=SEEDED_IBAN,
            bic="ETHNGRAA",
            account_holder="Acme Components",
        ),
    )
    receiver_authenticator = ReceiverAuthenticator(
        {
            RECEIVER_CREDENTIAL: ReceiverIdentity(
                caller_reference=INITIATOR_AUDIENCE,
                tenant_reference=TENANT_REFERENCE,
            )
        },
        required_caller=INITIATOR_AUDIENCE,
    )
    receiver_app = create_receiver_app(
        master,
        receiver_audience=RECEIVER_AUDIENCE,
        initiator_audience=INITIATOR_AUDIENCE,
        authenticator=receiver_authenticator,
    )
    transport = SupplierMasterTransport(
        receiver_app,
        receiver_audience=RECEIVER_AUDIENCE,
        response_audience=INITIATOR_AUDIENCE,
        credential=RECEIVER_CREDENTIAL,
    )
    identifiers = SequenceIdentifiers()
    secrets = VaultedExampleSecrets()
    clock = ExampleClock()
    store = MemoryActionStore()
    runtime = ActionRuntime(
        store=store,
        retention_store=store,
        clock=clock,
        identifiers=identifiers,
    )
    destination_ports = DestinationPorts(transport, identifiers, extractions)
    payment_ports = PaymentPorts(transport, identifiers)
    destination_definition = ActionDefinition(
        action_type=DESTINATION_ACTION,
        command_model=DestinationChangeCommand,
        private_snapshot_model=DestinationChangeSnapshot,
        display_preview_model=DestinationChangePreview,
        result_model=DestinationChangeResult,
        preparation=destination_ports,
        authorization=destination_ports,
        authority_evaluator=destination_ports,
        state_resolver=destination_ports,
        executor=destination_ports,
        verifier=destination_ports,
        commitment_provider=secrets,
        protection_codec=secrets,
        retention=destination_ports,
        proposal_ttl=timedelta(minutes=10),
        verification_delay=timedelta(0),
        max_verification_attempts=3,
        effect_kind="single",
        allow_resend_after_final_absence=False,
        executor_identity=GovernedExecutor(reference=INITIATOR_AUDIENCE),
        target_identity=AuthoritativeTarget(reference=RECEIVER_AUDIENCE),
        authority_audience=INITIATOR_AUDIENCE,
        authority_channel_assurance="out_of_band_exact_match",
    )
    payment_definition = ActionDefinition(
        action_type=PAYMENT_ACTION,
        command_model=PaymentCommand,
        private_snapshot_model=PaymentSnapshot,
        display_preview_model=PaymentPreview,
        result_model=PaymentResult,
        preparation=payment_ports,
        authorization=payment_ports,
        authority_evaluator=payment_ports,
        state_resolver=payment_ports,
        executor=payment_ports,
        verifier=payment_ports,
        commitment_provider=secrets,
        protection_codec=secrets,
        retention=payment_ports,
        proposal_ttl=timedelta(minutes=10),
        verification_delay=timedelta(0),
        max_verification_attempts=3,
        effect_kind="single",
        allow_resend_after_final_absence=False,
        executor_identity=GovernedExecutor(reference=INITIATOR_AUDIENCE),
        target_identity=AuthoritativeTarget(reference=RECEIVER_AUDIENCE),
        authority_audience=INITIATOR_AUDIENCE,
        authority_channel_assurance="authenticated_session",
    )
    application = SupplierDestinationApplication(
        runtime=runtime,
        store=store,
        clock=clock,
        secrets=secrets,
        identifiers=identifiers,
        extractions=extractions,
        oob_verifications=OOBVerificationRegistry(),
        destination_definition=destination_definition,
        payment_definition=payment_definition,
    )
    initiator_authenticator = InitiatorAuthenticator(
        {
            REQUESTER_CREDENTIAL: REQUESTER_IDENTITY,
            APPROVER_CREDENTIAL: APPROVER_IDENTITY,
            VERIFIER_CREDENTIAL: VERIFIER_IDENTITY,
            PAYMENT_CREDENTIAL: PAYMENT_IDENTITY,
        }
    )
    return SupplierDestinationExample(
        application=application,
        master=master,
        transport=transport,
        extractions=extractions,
        receiver_app=receiver_app,
        initiator_app=create_initiator_app(
            application,
            authenticator=initiator_authenticator,
        ),
    )
