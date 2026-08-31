"""In-memory authoritative supplier master used by the two-service example."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Annotated

from pydantic import Field, JsonValue

from threvo_actions.canonical import canonicalize_v1
from threvo_actions.models import ExperimentalModel, Money, SafeReference

from .domain import (
    BankDestination,
    EffectKind,
    ReceiverDestinationResult,
    ReceiverMutationStatus,
    ReceiverPaymentResult,
    ReceiverState,
)

SEEDED_IBAN = "GR1601101250000000012300695"
SEEDED_INTERNAL_SUPPLIER_ID = "01913f5d-89d8-7a8f-b8a4-11384773f66a"


class SupplierNotFoundError(LookupError):
    """Indistinguishable missing-supplier result for the private receiver boundary."""

    def __init__(self) -> None:
        super().__init__("supplier not found")


class SupplierRecord(ExperimentalModel):
    tenant_reference: SafeReference
    internal_supplier_id: SafeReference
    supplier_reference: SafeReference
    supplier_version: Annotated[int, Field(ge=1)]
    destination: BankDestination
    destination_version: Annotated[int, Field(ge=1)]
    verified_destination_version: Annotated[int, Field(ge=1)]


class RecordedEffect(ExperimentalModel):
    request_digest: SafeReference
    request_binding: SafeReference
    destination_result: ReceiverDestinationResult | None = None
    payment_result: ReceiverPaymentResult | None = None


class FakeSupplierMaster:
    """A lock-guarded stand-in for receiver-local lookup, CAS, and query."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._suppliers: dict[tuple[str, str], SupplierRecord] = {}
        self._effects: dict[tuple[str, EffectKind, str], RecordedEffect] = {}
        self._payment_sequence = 0
        self.mutate_at_next_destination_commit = False

    async def seed(self, record: SupplierRecord) -> None:
        async with self._lock:
            self._suppliers[(record.tenant_reference, record.supplier_reference)] = record

    async def state(self, *, tenant_reference: str, supplier_reference: str) -> ReceiverState:
        async with self._lock:
            record = self._required(tenant_reference, supplier_reference)
            return ReceiverState(
                supplier_reference=record.supplier_reference,
                supplier_version=record.supplier_version,
                verified_destination_version=record.verified_destination_version,
            )

    async def private_record(
        self, *, tenant_reference: str, supplier_reference: str
    ) -> SupplierRecord:
        async with self._lock:
            return self._required(tenant_reference, supplier_reference).model_copy(deep=True)

    async def externally_advance_supplier(
        self,
        *,
        tenant_reference: str,
        supplier_reference: str,
    ) -> None:
        async with self._lock:
            current = self._required(tenant_reference, supplier_reference)
            self._suppliers[(tenant_reference, supplier_reference)] = current.model_copy(
                update={"supplier_version": current.supplier_version + 1}
            )

    async def change_destination(
        self,
        *,
        tenant_reference: str,
        supplier_reference: str,
        expected_supplier_version: int,
        destination: BankDestination,
        semantic_effect_reference: str,
        request_binding: str,
    ) -> ReceiverDestinationResult:
        effect_key = (
            tenant_reference,
            EffectKind.DESTINATION_CHANGE,
            semantic_effect_reference,
        )
        request_payload: dict[str, JsonValue] = {
            "supplier_reference": supplier_reference,
            "expected_supplier_version": expected_supplier_version,
            "destination": destination.model_dump(mode="json"),
        }
        request_digest = self._request_digest(request_payload)
        async with self._lock:
            existing = self._effects.get(effect_key)
            if existing is not None and existing.destination_result is not None:
                if (
                    existing.request_digest != request_digest
                    or existing.request_binding != request_binding
                ):
                    return ReceiverDestinationResult(
                        status=ReceiverMutationStatus.PRECONDITION_FAILED,
                        reason_code="target_idempotency_conflict",
                    )
                return existing.destination_result
            current = self._required(tenant_reference, supplier_reference)
            if self.mutate_at_next_destination_commit:
                self.mutate_at_next_destination_commit = False
                current = current.model_copy(
                    update={"supplier_version": current.supplier_version + 1}
                )
                self._suppliers[(tenant_reference, supplier_reference)] = current
            if current.supplier_version != expected_supplier_version:
                return ReceiverDestinationResult(
                    status=ReceiverMutationStatus.PRECONDITION_FAILED,
                    reason_code="supplier_version_changed",
                )
            next_supplier_version = current.supplier_version + 1
            next_destination_version = current.destination_version + 1
            updated = current.model_copy(
                update={
                    "supplier_version": next_supplier_version,
                    "destination": destination,
                    "destination_version": next_destination_version,
                    "verified_destination_version": next_destination_version,
                }
            )
            self._suppliers[(tenant_reference, supplier_reference)] = updated
            result = ReceiverDestinationResult(
                status=ReceiverMutationStatus.ACCEPTED,
                supplier_reference=updated.supplier_reference,
                verified_destination_version=updated.verified_destination_version,
            )
            self._effects[effect_key] = RecordedEffect(
                request_digest=request_digest,
                request_binding=request_binding,
                destination_result=result,
            )
            return result

    async def release_payment(
        self,
        *,
        tenant_reference: str,
        supplier_reference: str,
        expected_supplier_version: int,
        verified_destination_version: int,
        amount: Money,
        semantic_effect_reference: str,
        request_binding: str,
    ) -> ReceiverPaymentResult:
        effect_key = (tenant_reference, EffectKind.PAYMENT_RELEASE, semantic_effect_reference)
        request_payload: dict[str, JsonValue] = {
            "supplier_reference": supplier_reference,
            "expected_supplier_version": expected_supplier_version,
            "verified_destination_version": verified_destination_version,
            "amount": amount.model_dump(mode="json"),
        }
        request_digest = self._request_digest(request_payload)
        async with self._lock:
            existing = self._effects.get(effect_key)
            if existing is not None and existing.payment_result is not None:
                if (
                    existing.request_digest != request_digest
                    or existing.request_binding != request_binding
                ):
                    return ReceiverPaymentResult(
                        status=ReceiverMutationStatus.PRECONDITION_FAILED,
                        reason_code="target_idempotency_conflict",
                    )
                return existing.payment_result
            current = self._required(tenant_reference, supplier_reference)
            destination_is_bound = (
                current.destination_version == verified_destination_version
                and current.verified_destination_version == verified_destination_version
            )
            if current.supplier_version != expected_supplier_version or not destination_is_bound:
                return ReceiverPaymentResult(
                    status=ReceiverMutationStatus.PRECONDITION_FAILED,
                    reason_code="verified_destination_changed",
                )
            self._payment_sequence += 1
            result = ReceiverPaymentResult(
                status=ReceiverMutationStatus.ACCEPTED,
                supplier_reference=current.supplier_reference,
                payment_reference=f"payment:{self._payment_sequence}",
                verified_destination_version=verified_destination_version,
            )
            self._effects[effect_key] = RecordedEffect(
                request_digest=request_digest,
                request_binding=request_binding,
                payment_result=result,
            )
            return result

    async def query_effect(
        self,
        *,
        tenant_reference: str,
        kind: EffectKind,
        semantic_effect_reference: str,
    ) -> RecordedEffect | None:
        async with self._lock:
            effect = self._effects.get((tenant_reference, kind, semantic_effect_reference))
            return effect.model_copy(deep=True) if effect is not None else None

    def _required(self, tenant_reference: str, supplier_reference: str) -> SupplierRecord:
        record = self._suppliers.get((tenant_reference, supplier_reference))
        if record is None:
            raise SupplierNotFoundError
        return record

    @staticmethod
    def _request_digest(value: JsonValue) -> str:
        return hashlib.sha256(canonicalize_v1(value)).hexdigest()
