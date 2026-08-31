"""Measure canonicalization and in-process preparation without external I/O."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import platform
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from threvo_actions import (
    ActionDefinition,
    ActionRuntime,
    ActionType,
    AuthoritativeTarget,
    AuthorityEvaluation,
    AuthorityEvidence,
    AuthorizationResult,
    DecisionContext,
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    GovernedExecutor,
    KeyedCommitment,
    MemoryActionStore,
    PreparationContext,
    PreparedAction,
    ProtectedPayload,
    ReadContext,
    RequestingPrincipal,
    ResolvedState,
    VerificationResult,
    VerificationStatus,
)
from threvo_actions.canonical import canonicalize_v1
from threvo_actions.conformance import (
    BenchmarkResult,
    PerformanceProfile,
    benchmark_async_concurrent,
    benchmark_sync,
)
from threvo_actions.models import ExperimentalModel, SafeReference

if TYPE_CHECKING:
    from pydantic import JsonValue


class BenchmarkCommand(ExperimentalModel):
    case_reference: SafeReference
    payload: str
    item_references: tuple[SafeReference, ...]


class BenchmarkSnapshot(ExperimentalModel):
    case_reference: SafeReference
    payload: str
    item_references: tuple[SafeReference, ...]


class BenchmarkPreview(ExperimentalModel):
    case_reference: SafeReference
    item_count: int


class BenchmarkActionResult(ExperimentalModel):
    result_reference: SafeReference


class SequenceIdentifiers:
    def __init__(self) -> None:
        self._value = 0

    def new(self, prefix: str) -> str:
        self._value += 1
        return f"{prefix}:benchmark:{self._value}"


class InProcessProtection:
    """Benchmark-only protection with no network, disk, or key-service I/O."""

    def __init__(self) -> None:
        self._key = b"synthetic-benchmark-only"
        self._payloads: dict[str, bytes] = {}

    async def create(self, *, proposal_reference: str, canonical_payload: bytes) -> KeyedCommitment:
        return KeyedCommitment(
            algorithm="hmac-sha256",
            key_handle=f"commitment:{proposal_reference}",
            key_version="benchmark-v1",
            digest=hmac.new(self._key, canonical_payload, hashlib.sha256).hexdigest(),
        )

    async def verify(
        self,
        *,
        proposal_reference: str,
        canonical_payload: bytes,
        commitment: KeyedCommitment,
    ) -> bool:
        del proposal_reference
        expected = hmac.new(self._key, canonical_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, commitment.digest)

    async def destroy_commitment(self, *, commitment: KeyedCommitment) -> None:
        del commitment

    async def protect(
        self,
        *,
        proposal_reference: str,
        canonical_payload: bytes,
    ) -> ProtectedPayload:
        handle = f"payload:{proposal_reference}"
        self._payloads[handle] = canonical_payload
        return ProtectedPayload(
            codec="benchmark-memory-v1",
            key_handle=handle,
            key_version="benchmark-v1",
            ciphertext=base64.b64encode(hashlib.sha256(canonical_payload).digest()).decode(),
        )

    async def unprotect(self, *, payload: ProtectedPayload) -> bytes:
        return self._payloads[payload.key_handle]

    async def destroy_payload(self, *, payload: ProtectedPayload) -> None:
        self._payloads.pop(payload.key_handle, None)


class BenchmarkHost:
    async def prepare(
        self,
        command: BenchmarkCommand,
        *,
        context: PreparationContext,
    ) -> PreparedAction[BenchmarkSnapshot, BenchmarkPreview]:
        del context
        snapshot = BenchmarkSnapshot(
            case_reference=command.case_reference,
            payload=command.payload,
            item_references=command.item_references,
        )
        return PreparedAction(
            private_snapshot=snapshot,
            display_preview=BenchmarkPreview(
                case_reference=command.case_reference,
                item_count=len(command.item_references),
            ),
            semantic_effect_reference=f"benchmark:{command.case_reference}",
        )

    async def can_prepare(
        self,
        command: BenchmarkCommand,
        *,
        context: PreparationContext,
    ) -> AuthorizationResult:
        del command, context
        return AuthorizationResult(allowed=True)

    async def can_decide(
        self,
        evidence: AuthorityEvidence,
        *,
        context: DecisionContext,
    ) -> AuthorizationResult:
        del evidence, context
        return AuthorizationResult(allowed=True)

    async def can_execute(
        self,
        snapshot: BenchmarkSnapshot,
        *,
        context: ExecutionContext,
    ) -> AuthorizationResult:
        del snapshot, context
        return AuthorizationResult(allowed=True)

    async def can_read(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference, context
        return True

    async def evaluate(
        self,
        *,
        binding: object,
        evidence: tuple[AuthorityEvidence, ...],
    ) -> AuthorityEvaluation:
        del binding, evidence
        return AuthorityEvaluation(satisfied=True)

    async def resolve(
        self,
        snapshot: BenchmarkSnapshot,
        *,
        context: ExecutionContext,
    ) -> ResolvedState[BenchmarkSnapshot, BenchmarkPreview]:
        del context
        return ResolvedState(
            current_snapshot=snapshot,
            execution_precondition="benchmark:unchanged",
            materially_drifted=False,
        )

    async def execute(
        self,
        snapshot: BenchmarkSnapshot,
        *,
        context: ExecutionContext,
        execution_precondition: str,
    ) -> ExecutionResult[BenchmarkActionResult]:
        del snapshot, context, execution_precondition
        return ExecutionResult[BenchmarkActionResult](
            status=ExecutionStatus.ACCEPTED,
            result=BenchmarkActionResult(result_reference="result:benchmark"),
        )

    async def verify(
        self,
        *,
        context: ExecutionContext,
    ) -> VerificationResult[BenchmarkActionResult]:
        del context
        return VerificationResult[BenchmarkActionResult](
            status=VerificationStatus.VERIFIED_COMPLETION,
            result=BenchmarkActionResult(result_reference="result:benchmark"),
        )

    async def authorize_erasure(self, proposal_reference: str, *, context: ReadContext) -> bool:
        del proposal_reference, context
        return True


@dataclass(frozen=True)
class BenchmarkApplication:
    runtime: ActionRuntime
    action: ActionDefinition[
        BenchmarkCommand,
        BenchmarkSnapshot,
        BenchmarkPreview,
        BenchmarkActionResult,
    ]


def build_benchmark_application() -> BenchmarkApplication:
    host = BenchmarkHost()
    protection = InProcessProtection()
    action = ActionDefinition(
        action_type=ActionType(namespace="example.benchmark", name="prepare", version=1),
        command_model=BenchmarkCommand,
        private_snapshot_model=BenchmarkSnapshot,
        display_preview_model=BenchmarkPreview,
        result_model=BenchmarkActionResult,
        preparation=host,
        authorization=host,
        authority_evaluator=host,
        state_resolver=host,
        executor=host,
        verifier=host,
        commitment_provider=protection,
        protection_codec=protection,
        retention=host,
        proposal_ttl=timedelta(minutes=10),
        verification_delay=timedelta(0),
        max_verification_attempts=1,
        effect_kind="single",
        allow_resend_after_final_absence=False,
        executor_identity=GovernedExecutor(reference="service:benchmark"),
        target_identity=AuthoritativeTarget(reference="target:benchmark"),
        authority_audience="service:benchmark",
        authority_channel_assurance="benchmark",
    )
    store = MemoryActionStore()
    runtime = ActionRuntime(
        store=store,
        retention_store=store,
        clock=FixedClock(),
        identifiers=SequenceIdentifiers(),
    )
    return BenchmarkApplication(runtime=runtime, action=action)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def canonical_document(*, payload: str, item_references: tuple[str, ...]) -> dict[str, JsonValue]:
    return {
        "case_reference": "case:benchmark",
        "payload": payload,
        "item_references": list(item_references),
    }


async def benchmark_preparation(
    *,
    payload: str,
    item_references: tuple[str, ...],
    profile: PerformanceProfile,
) -> BenchmarkResult:
    application = build_benchmark_application()

    async def operation(index: int) -> None:
        await application.runtime.prepare(
            application.action,
            tenant_reference="tenant:benchmark",
            command=BenchmarkCommand(
                case_reference=f"case:{index}",
                payload=payload,
                item_references=item_references,
            ),
            requesting_principal=RequestingPrincipal(reference="user:benchmark"),
        )

    return await benchmark_async_concurrent(
        operation,
        profile=profile,
        concurrency=100,
        batches=10,
        warmup_batches=1,
    )


async def run() -> dict[str, object]:
    four_kib = "x" * 4096
    bulk_items = tuple(f"item:{index}" for index in range(500))
    canonical_profile = PerformanceProfile(
        name="canonicalization-4-kib",
        max_p95_ms=5,
        max_p99_ms=10,
        min_iterations=1000,
    )
    bulk_canonical_profile = PerformanceProfile(
        name="canonicalization-500-items",
        max_p95_ms=10,
        max_p99_ms=20,
        min_iterations=1000,
    )
    prepare_profile = PerformanceProfile(
        name="prepare-4-kib-100-concurrent",
        max_p95_ms=10,
        max_p99_ms=25,
        min_iterations=1000,
    )
    bulk_prepare_profile = PerformanceProfile(
        name="prepare-500-items-100-concurrent",
        max_p95_ms=25,
        max_p99_ms=50,
        min_iterations=1000,
    )
    four_kib_document = canonical_document(payload=four_kib, item_references=())
    bulk_document = canonical_document(payload="", item_references=bulk_items)
    canonical_4k = benchmark_sync(
        lambda: canonicalize_v1(four_kib_document),
        profile=canonical_profile,
    )
    canonical_bulk = benchmark_sync(
        lambda: canonicalize_v1(bulk_document),
        profile=bulk_canonical_profile,
    )
    prepare_4k = await benchmark_preparation(
        payload=four_kib,
        item_references=(),
        profile=prepare_profile,
    )
    prepare_bulk = await benchmark_preparation(
        payload="",
        item_references=bulk_items,
        profile=bulk_prepare_profile,
    )
    return {
        "methodology": {
            "warmup_batches": 1,
            "measured_batches": 10,
            "concurrency": 100,
            "samples_per_profile": 1000,
            "external_io": False,
            "store": "MemoryActionStore",
            "provider": "in-process benchmark-only protection",
            "clock": "fixed",
        },
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "results": [
            asdict(canonical_4k),
            asdict(prepare_4k),
            asdict(canonical_bulk),
            asdict(prepare_bulk),
        ],
    }


def main() -> None:
    print(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
