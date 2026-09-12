from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

import pytest

from threvo_actions.integrations.stripe import (
    StripeHostActionGroup,
    StripeHostCloseStatus,
    StripeHostConformanceError,
    StripeHostExerciseDescriptor,
    StripeHostExerciseIntent,
    StripeHostIntentObservation,
    StripeHostIntentPhase,
    StripeHostNormalWriteCheckpoint,
    StripeHostNormalWriteStatus,
    StripeHostRememberStatus,
    StripeHostReserveStatus,
    StripeHostScenario,
    assert_stripe_host_exercise,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from threvo_actions.integrations.stripe import StripeHostExerciseAdapter


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


class ExercisedStore:
    descriptor = StripeHostExerciseDescriptor(
        action_group=StripeHostActionGroup.REFUNDS,
        profile_identifier="memory:library-orchestrated-test",
    )

    def __init__(self) -> None:
        self.entries: dict[
            tuple[str, StripeHostActionGroup, str],
            tuple[
                StripeHostExerciseIntent,
                StripeHostIntentPhase,
                Literal["no_submission", "outcome"] | None,
                str | None,
            ],
        ] = {}
        self.resource_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.resource_revisions: dict[tuple[str, str], int] = {}
        self.remembered_revisions: dict[tuple[str, StripeHostActionGroup, str], int] = {}
        self.calls: list[str] = []

    async def reset(self, scenario: StripeHostScenario) -> None:
        self.entries.clear()
        self.resource_locks.clear()
        self.resource_revisions.clear()
        self.remembered_revisions.clear()
        self.calls.append(f"reset:{scenario.value}")

    async def remember(self, intent: StripeHostExerciseIntent) -> StripeHostRememberStatus:
        self.calls.append("remember")
        key = (intent.tenant_reference, intent.action_group, intent.effect_reference)
        current = self.entries.get(key)
        if current is None:
            self.entries[key] = (intent, StripeHostIntentPhase.READY, None, None)
            resource_key = (intent.tenant_reference, intent.resource_reference)
            self.resource_revisions.setdefault(resource_key, 0)
            self.remembered_revisions[key] = self.resource_revisions[resource_key]
            return StripeHostRememberStatus.CREATED
        if current[0] == intent:
            return StripeHostRememberStatus.MATCHED
        return StripeHostRememberStatus.CONFLICT

    async def load(
        self,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
    ) -> StripeHostIntentObservation | None:
        self.calls.append("load")
        current = self.entries.get((tenant_reference, action_group, effect_reference))
        if current is None:
            return None
        intent, phase, close_kind, outcome_digest = current
        return StripeHostIntentObservation(
            tenant_reference=intent.tenant_reference,
            action_group=intent.action_group,
            effect_reference=intent.effect_reference,
            resource_reference=intent.resource_reference,
            requester_reference=intent.requester_reference,
            snapshot_digest=intent.snapshot_digest,
            phase=phase,
            close_kind=close_kind,
            outcome_digest=outcome_digest,
        )

    async def reserve(
        self,
        intent: StripeHostExerciseIntent,
        *,
        not_after: datetime,
        simulate_lost_acknowledgement: bool = False,
    ) -> StripeHostReserveStatus:
        self.calls.append("reserve")
        lock_key = (intent.tenant_reference, intent.resource_reference)
        lock = self.resource_locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            key = (intent.tenant_reference, intent.action_group, intent.effect_reference)
            current = self.entries.get(key)
            if current is None or current[0] != intent:
                return StripeHostReserveStatus.STALE
            resource_key = (intent.tenant_reference, intent.resource_reference)
            if self.resource_revisions[resource_key] != self.remembered_revisions[key]:
                return StripeHostReserveStatus.STALE
            if current[1] is not StripeHostIntentPhase.READY:
                return StripeHostReserveStatus.ALREADY_SUBMITTED
            if not_after <= FixedClock().now():
                return StripeHostReserveStatus.STALE
            if any(
                other_intent.tenant_reference == intent.tenant_reference
                and other_intent.resource_reference == intent.resource_reference
                and other_phase is StripeHostIntentPhase.RESERVED
                and other_key != key
                for other_key, (
                    other_intent,
                    other_phase,
                    _,
                    _,
                ) in self.entries.items()
            ):
                return StripeHostReserveStatus.UNAVAILABLE
            self.entries[key] = (intent, StripeHostIntentPhase.RESERVED, None, None)
            if simulate_lost_acknowledgement:
                return StripeHostReserveStatus.ACKNOWLEDGEMENT_LOST
            return StripeHostReserveStatus.ACQUIRED

    async def record_no_submission(self, intent: StripeHostExerciseIntent) -> StripeHostCloseStatus:
        return await self._close(intent, close_kind="no_submission", outcome_data=None)

    async def record_outcome(
        self,
        intent: StripeHostExerciseIntent,
        *,
        outcome_data: dict[str, JsonValue],
    ) -> StripeHostCloseStatus:
        return await self._close(intent, close_kind="outcome", outcome_data=outcome_data)

    async def _close(
        self,
        intent: StripeHostExerciseIntent,
        *,
        close_kind: Literal["no_submission", "outcome"],
        outcome_data: dict[str, JsonValue] | None,
    ) -> StripeHostCloseStatus:
        self.calls.append("close")
        key = (intent.tenant_reference, intent.action_group, intent.effect_reference)
        current = self.entries[key]
        digest = (
            None
            if outcome_data is None
            else hashlib.sha256(
                json.dumps(outcome_data, separators=(",", ":"), sort_keys=True).encode()
            ).hexdigest()
        )
        if current[1] is StripeHostIntentPhase.CLOSED:
            if current[2:] == (close_kind, digest):
                return StripeHostCloseStatus.MATCHED
            return StripeHostCloseStatus.CONFLICT
        self.entries[key] = (intent, StripeHostIntentPhase.CLOSED, close_kind, digest)
        return StripeHostCloseStatus.RECORDED

    async def normal_write(
        self,
        *,
        tenant_reference: str,
        resource_reference: str,
        checkpoint: StripeHostNormalWriteCheckpoint,
    ) -> StripeHostNormalWriteStatus:
        self.calls.append("normal_write")
        resource_key = (tenant_reference, resource_reference)
        lock = self.resource_locks.setdefault(resource_key, asyncio.Lock())
        async with lock:
            if any(
                intent.tenant_reference == tenant_reference
                and intent.resource_reference == resource_reference
                and phase is StripeHostIntentPhase.RESERVED
                for intent, phase, _, _ in self.entries.values()
            ):
                return StripeHostNormalWriteStatus.REFUSED
            await checkpoint.after_reservation_check()
            self.resource_revisions[resource_key] += 1
            return StripeHostNormalWriteStatus.APPLIED


def test_library_executes_and_decides_every_scenario() -> None:
    adapter = ExercisedStore()

    report = asyncio.run(assert_stripe_host_exercise(adapter, clock=FixedClock()))

    assert report.passed is True
    assert report.execution_basis == "library_orchestrated"
    assert report.schema_version == "stripe-host-exercise/v1"
    assert tuple(result.scenario for result in report.results) == tuple(StripeHostScenario)
    assert adapter.calls.count("normal_write") == 2
    assert adapter.calls.count("reserve") >= 14


def test_adapter_cannot_self_declare_a_scenario_passed() -> None:
    class SelfDeclaringDriver(ExercisedStore):
        def __init__(self) -> None:
            super().__init__()
            self.self_attestation_calls = 0

        async def run_scenario(self, scenario: StripeHostScenario) -> object:
            self.self_attestation_calls += 1
            return {"scenario": scenario, "disposition": "passed"}

    adapter = SelfDeclaringDriver()
    report = asyncio.run(
        assert_stripe_host_exercise(
            cast("StripeHostExerciseAdapter", adapter),
            clock=FixedClock(),
        )
    )

    assert report.passed is True
    assert adapter.self_attestation_calls == 0
    assert adapter.calls.count("reserve") >= 14


def test_library_rejects_a_broken_race_implementation() -> None:
    class BrokenRaceStore(ExercisedStore):
        async def reserve(
            self,
            intent: StripeHostExerciseIntent,
            *,
            not_after: datetime,
            simulate_lost_acknowledgement: bool = False,
        ) -> StripeHostReserveStatus:
            if "same_effect_race" in intent.effect_reference:
                return StripeHostReserveStatus.ACQUIRED
            return await super().reserve(
                intent,
                not_after=not_after,
                simulate_lost_acknowledgement=simulate_lost_acknowledgement,
            )

    with pytest.raises(StripeHostConformanceError) as captured:
        asyncio.run(assert_stripe_host_exercise(BrokenRaceStore(), clock=FixedClock()))

    assert captured.value.code == "stripe_host:same_effect_race:race_not_serialized"


@pytest.mark.parametrize(
    ("scenario_name", "expected_code"),
    [
        (
            "closed_intent_non_reopening",
            "stripe_host:closed_intent_non_reopening:reserve_failed",
        ),
        ("outcome_idempotence", "stripe_host:outcome_idempotence:reserve_failed"),
    ],
)
def test_library_refuses_to_close_without_acquiring_reservation(
    scenario_name: str,
    expected_code: str,
) -> None:
    class MissingAdmissionStore(ExercisedStore):
        async def reserve(
            self,
            intent: StripeHostExerciseIntent,
            *,
            not_after: datetime,
            simulate_lost_acknowledgement: bool = False,
        ) -> StripeHostReserveStatus:
            if scenario_name in intent.effect_reference:
                return StripeHostReserveStatus.STALE
            return await super().reserve(
                intent,
                not_after=not_after,
                simulate_lost_acknowledgement=simulate_lost_acknowledgement,
            )

    with pytest.raises(StripeHostConformanceError) as captured:
        asyncio.run(assert_stripe_host_exercise(MissingAdmissionStore(), clock=FixedClock()))

    assert captured.value.code == expected_code


def test_library_rejects_a_non_atomic_normal_writer() -> None:
    class NonAtomicWriterStore(ExercisedStore):
        async def normal_write(
            self,
            *,
            tenant_reference: str,
            resource_reference: str,
            checkpoint: StripeHostNormalWriteCheckpoint,
        ) -> StripeHostNormalWriteStatus:
            resource_key = (tenant_reference, resource_reference)
            if any(
                intent.tenant_reference == tenant_reference
                and intent.resource_reference == resource_reference
                and phase is StripeHostIntentPhase.RESERVED
                for intent, phase, _, _ in self.entries.values()
            ):
                return StripeHostNormalWriteStatus.REFUSED
            await checkpoint.after_reservation_check()
            self.resource_revisions[resource_key] += 1
            return StripeHostNormalWriteStatus.APPLIED

    with pytest.raises(StripeHostConformanceError) as captured:
        asyncio.run(assert_stripe_host_exercise(NonAtomicWriterStore(), clock=FixedClock()))

    assert captured.value.code == (
        "stripe_host:normal_writer_exclusion:reservation_bypassed_writer_lock"
    )
