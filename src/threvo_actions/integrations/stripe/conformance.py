"""Honest host attestations and library-orchestrated Stripe exercises."""

from __future__ import annotations

import asyncio
import hashlib
import json
import warnings
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from ...evidence import FrozenJsonObject
from ...models import ActionModel, SafeReference
from ...runtime import Clock, SystemClock

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class StripeHostActionGroup(StrEnum):
    """Repository contract exercised by a conformance adapter."""

    REFUNDS = "refunds"
    SUBSCRIPTIONS = "subscriptions"
    CREDIT_NOTES = "credit_notes"


class StripeHostScenario(StrEnum):
    """Closed scenario vocabulary for the v1 host exercise."""

    IMMUTABLE_INTENT = "immutable_intent"
    TENANT_ISOLATION = "tenant_isolation"
    SAME_EFFECT_RACE = "same_effect_race"
    CONFLICTING_RESOURCE_RACE = "conflicting_resource_race"
    UNRELATED_RESOURCE_PROGRESS = "unrelated_resource_progress"
    NORMAL_WRITER_EXCLUSION = "normal_writer_exclusion"
    LOST_RESERVATION_ACKNOWLEDGEMENT = "lost_reservation_acknowledgement"
    EXPIRED_ADMISSION = "expired_admission"
    CLOSED_INTENT_NON_REOPENING = "closed_intent_non_reopening"
    OUTCOME_IDEMPOTENCE = "outcome_idempotence"
    DELAYED_CLOSURE_OWNER_SAFETY = "delayed_closure_owner_safety"
    DIAGNOSTIC_MINIMIZATION = "diagnostic_minimization"


class StripeHostScenarioDisposition(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_EXERCISED = "not_exercised"


class StripeHostCapabilities(ActionModel):
    """Capabilities claimed by the legacy driver-attestation format."""

    independent_connections: bool
    normal_application_writer: bool
    lost_acknowledgement_injection: bool


class StripeHostConformanceDescriptor(ActionModel):
    """Identity and capabilities declared by a legacy adopter fixture."""

    action_group: StripeHostActionGroup
    profile_identifier: SafeReference
    capabilities: StripeHostCapabilities


class StripeHostScenarioResult(ActionModel):
    """Content-safe result for one attested or library-exercised scenario."""

    scenario: StripeHostScenario
    disposition: StripeHostScenarioDisposition
    reason_code: SafeReference | None = None

    @model_validator(mode="after")
    def failure_has_reason(self) -> StripeHostScenarioResult:
        if self.disposition is StripeHostScenarioDisposition.PASSED:
            if self.reason_code is not None:
                raise ValueError("passed scenario cannot carry a reason code")
        elif self.reason_code is None:
            raise ValueError("failed or unexercised scenario requires a reason code")
        return self


class StripeHostConformanceReport(ActionModel):
    """Legacy driver attestation; it does not establish repository conformance."""

    schema_version: Literal["stripe-host-conformance/v1"] = "stripe-host-conformance/v1"
    assessment_basis: Literal["driver_attestation"] = "driver_attestation"
    conformance_established: Literal[False] = False
    action_group: StripeHostActionGroup
    profile_identifier: SafeReference
    capabilities: StripeHostCapabilities
    results: tuple[StripeHostScenarioResult, ...] = Field(min_length=1)

    @property
    def passed(self) -> bool:
        """Whether every driver claim says passed, not proof that tests ran."""

        return all(
            result.disposition is StripeHostScenarioDisposition.PASSED for result in self.results
        )


class StripeHostConformanceError(AssertionError):
    """Stable exercise failure that never includes adapter exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class StripeHostConformanceDriver(Protocol):
    """Legacy driver that self-reports scenario dispositions."""

    @property
    def descriptor(self) -> StripeHostConformanceDescriptor: ...

    async def run_scenario(self, scenario: StripeHostScenario) -> StripeHostScenarioResult: ...


_REQUIRED_SCENARIOS = tuple(StripeHostScenario)


def _require_capabilities(descriptor: StripeHostConformanceDescriptor) -> None:
    capabilities = descriptor.capabilities
    missing = (
        ("independent_connections", not capabilities.independent_connections),
        ("normal_application_writer", not capabilities.normal_application_writer),
        ("lost_acknowledgement_injection", not capabilities.lost_acknowledgement_injection),
    )
    for name, absent in missing:
        if absent:
            raise StripeHostConformanceError(f"stripe_host:capability:{name}:not_exercised")


async def collect_stripe_host_attestation(
    driver: StripeHostConformanceDriver,
) -> StripeHostConformanceReport:
    """Validate a complete driver attestation without claiming tests were executed."""

    descriptor = driver.descriptor
    _require_capabilities(descriptor)
    results: list[StripeHostScenarioResult] = []
    for scenario in _REQUIRED_SCENARIOS:
        try:
            result = await driver.run_scenario(scenario)
        except Exception:
            raise StripeHostConformanceError(f"stripe_host:{scenario.value}:driver_error") from None
        if result.scenario is not scenario:
            raise StripeHostConformanceError(f"stripe_host:{scenario.value}:mismatched_result")
        if result.disposition is not StripeHostScenarioDisposition.PASSED:
            raise StripeHostConformanceError(
                f"stripe_host:{scenario.value}:{result.disposition.value}"
            )
        results.append(result)
    return StripeHostConformanceReport(
        action_group=descriptor.action_group,
        profile_identifier=descriptor.profile_identifier,
        capabilities=descriptor.capabilities,
        results=tuple(results),
    )


async def assert_stripe_host_conforms(
    driver: StripeHostConformanceDriver,
) -> StripeHostConformanceReport:
    """Deprecated compatibility wrapper for the legacy self-attestation format."""

    warnings.warn(
        "assert_stripe_host_conforms validates driver claims but does not execute "
        "repository tests; use assert_stripe_host_exercise",
        DeprecationWarning,
        stacklevel=2,
    )
    return await collect_stripe_host_attestation(driver)


class StripeHostRememberStatus(StrEnum):
    CREATED = "created"
    MATCHED = "matched"
    CONFLICT = "conflict"
    NOT_EXERCISED = "not_exercised"


class StripeHostReserveStatus(StrEnum):
    ACQUIRED = "acquired"
    ALREADY_SUBMITTED = "already_submitted"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    ACKNOWLEDGEMENT_LOST = "acknowledgement_lost"
    NOT_EXERCISED = "not_exercised"


class StripeHostCloseStatus(StrEnum):
    RECORDED = "recorded"
    MATCHED = "matched"
    CONFLICT = "conflict"
    NOT_EXERCISED = "not_exercised"


class StripeHostNormalWriteStatus(StrEnum):
    APPLIED = "applied"
    REFUSED = "refused"
    NOT_EXERCISED = "not_exercised"


class StripeHostIntentPhase(StrEnum):
    READY = "ready"
    RESERVED = "reserved"
    CLOSED = "closed"


class StripeHostNormalWriteCheckpoint(Protocol):
    """Library-owned interlock reached after the writer checks reservations."""

    async def after_reservation_check(self) -> None: ...


class StripeHostExerciseDescriptor(ActionModel):
    """Identity of the concrete fixture exercised by the library."""

    action_group: StripeHostActionGroup
    profile_identifier: SafeReference


class StripeHostExerciseIntent(ActionModel):
    """Provider-neutral intent supplied to an adopter's exercise adapter."""

    tenant_reference: SafeReference
    action_group: StripeHostActionGroup
    effect_reference: SafeReference
    resource_reference: SafeReference
    requester_reference: SafeReference
    snapshot_data: FrozenJsonObject

    def snapshot_mapping(self) -> dict[str, JsonValue]:
        """Return a validated copy for an adapter's persistence boundary."""

        return _JSON_OBJECT.validate_python(self.snapshot_data.model_dump(mode="json"))

    @property
    def snapshot_digest(self) -> str:
        encoded = json.dumps(
            self.snapshot_mapping(),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


class StripeHostIntentObservation(ActionModel):
    """Minimal authoritative state returned by an exercise adapter."""

    tenant_reference: SafeReference
    action_group: StripeHostActionGroup
    effect_reference: SafeReference
    resource_reference: SafeReference
    requester_reference: SafeReference
    snapshot_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    phase: StripeHostIntentPhase
    close_kind: Literal["no_submission", "outcome"] | None = None
    outcome_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class StripeHostExerciseAdapter(Protocol):
    """Primitive operations invoked and evaluated by the library exercise."""

    @property
    def descriptor(self) -> StripeHostExerciseDescriptor: ...

    async def reset(self, scenario: StripeHostScenario) -> None: ...

    async def remember(self, intent: StripeHostExerciseIntent) -> StripeHostRememberStatus: ...

    async def load(
        self,
        *,
        tenant_reference: str,
        action_group: StripeHostActionGroup,
        effect_reference: str,
    ) -> StripeHostIntentObservation | None: ...

    async def reserve(
        self,
        intent: StripeHostExerciseIntent,
        *,
        not_after: datetime,
        simulate_lost_acknowledgement: bool = False,
    ) -> StripeHostReserveStatus: ...

    async def record_no_submission(
        self, intent: StripeHostExerciseIntent
    ) -> StripeHostCloseStatus: ...

    async def record_outcome(
        self,
        intent: StripeHostExerciseIntent,
        *,
        outcome_data: dict[str, JsonValue],
    ) -> StripeHostCloseStatus: ...

    async def normal_write(
        self,
        *,
        tenant_reference: str,
        resource_reference: str,
        checkpoint: StripeHostNormalWriteCheckpoint,
    ) -> StripeHostNormalWriteStatus: ...


class StripeHostExerciseReport(ActionModel):
    """Evidence produced after library-owned scenario execution and assertions."""

    schema_version: Literal["stripe-host-exercise/v1"] = "stripe-host-exercise/v1"
    execution_basis: Literal["library_orchestrated"] = "library_orchestrated"
    action_group: StripeHostActionGroup
    profile_identifier: SafeReference
    results: tuple[StripeHostScenarioResult, ...] = Field(min_length=1)

    @property
    def passed(self) -> bool:
        return all(
            result.disposition is StripeHostScenarioDisposition.PASSED for result in self.results
        )


def _intent(
    descriptor: StripeHostExerciseDescriptor,
    scenario: StripeHostScenario,
    *,
    suffix: str = "one",
    action_group: StripeHostActionGroup | None = None,
    resource_suffix: str = "one",
    snapshot_marker: str = "original",
) -> StripeHostExerciseIntent:
    return StripeHostExerciseIntent(
        tenant_reference=f"exercise-tenant:{scenario.value}",
        action_group=action_group or descriptor.action_group,
        effect_reference=f"exercise-effect:{scenario.value}:{suffix}",
        resource_reference=f"exercise-resource:{scenario.value}:{resource_suffix}",
        requester_reference="exercise-requester:one",
        snapshot_data=FrozenJsonObject.from_mapping(
            {
                "marker": snapshot_marker,
                "amount": "10.00",
                "private_sentinel": "must-not-appear-in-report",
            }
        ),
    )


def _other_group(group: StripeHostActionGroup) -> StripeHostActionGroup:
    if group is StripeHostActionGroup.REFUNDS:
        return StripeHostActionGroup.CREDIT_NOTES
    return StripeHostActionGroup.REFUNDS


def _outcome_digest(value: dict[str, JsonValue]) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _require(scenario: StripeHostScenario, condition: bool, code: str) -> None:
    if not condition:
        raise StripeHostConformanceError(f"stripe_host:{scenario.value}:{code}")


class _NormalWriteRaceCheckpoint:
    def __init__(self) -> None:
        self._checked = asyncio.Event()
        self._continue = asyncio.Event()

    async def after_reservation_check(self) -> None:
        self._checked.set()
        await self._continue.wait()

    async def wait_until_checked(self) -> None:
        await self._checked.wait()

    def release(self) -> None:
        self._continue.set()


async def _exercise_scenario(
    adapter: StripeHostExerciseAdapter,
    scenario: StripeHostScenario,
    *,
    clock: Clock,
) -> None:
    await adapter.reset(scenario)
    descriptor = adapter.descriptor
    first = _intent(descriptor, scenario)
    now = clock.now()
    future = now + timedelta(minutes=5)

    if scenario is StripeHostScenario.IMMUTABLE_INTENT:
        created = await adapter.remember(first)
        changed = first.model_copy(
            update={
                "snapshot_data": FrozenJsonObject.from_mapping(
                    {**first.snapshot_mapping(), "marker": "changed"}
                )
            }
        )
        conflict = await adapter.remember(changed)
        observed = await adapter.load(
            tenant_reference=first.tenant_reference,
            action_group=first.action_group,
            effect_reference=first.effect_reference,
        )
        _require(scenario, created is StripeHostRememberStatus.CREATED, "create_not_observed")
        _require(scenario, conflict is StripeHostRememberStatus.CONFLICT, "binding_reopened")
        if observed is None:
            raise StripeHostConformanceError(f"stripe_host:{scenario.value}:intent_missing")
        _require(scenario, observed.snapshot_digest == first.snapshot_digest, "binding_changed")
        return

    if scenario is StripeHostScenario.TENANT_ISOLATION:
        _require(
            scenario,
            await adapter.remember(first) is StripeHostRememberStatus.CREATED,
            "create_not_observed",
        )
        hidden = await adapter.load(
            tenant_reference="exercise-tenant:other",
            action_group=first.action_group,
            effect_reference=first.effect_reference,
        )
        visible = await adapter.load(
            tenant_reference=first.tenant_reference,
            action_group=first.action_group,
            effect_reference=first.effect_reference,
        )
        _require(scenario, hidden is None, "cross_tenant_visible")
        _require(scenario, visible is not None, "owner_cannot_read")
        return

    if scenario is StripeHostScenario.SAME_EFFECT_RACE:
        await adapter.remember(first)
        results = await asyncio.gather(
            adapter.reserve(first, not_after=future),
            adapter.reserve(first, not_after=future),
        )
        _require(
            scenario,
            results.count(StripeHostReserveStatus.ACQUIRED) == 1
            and results.count(StripeHostReserveStatus.ALREADY_SUBMITTED) == 1,
            "race_not_serialized",
        )
        return

    if scenario is StripeHostScenario.CONFLICTING_RESOURCE_RACE:
        second = _intent(
            descriptor,
            scenario,
            suffix="two",
            action_group=_other_group(first.action_group),
        )
        await adapter.remember(first)
        await adapter.remember(second)
        results = await asyncio.gather(
            adapter.reserve(first, not_after=future),
            adapter.reserve(second, not_after=future),
        )
        _require(
            scenario,
            results.count(StripeHostReserveStatus.ACQUIRED) == 1
            and results.count(StripeHostReserveStatus.UNAVAILABLE) == 1,
            "resource_race_not_serialized",
        )
        return

    if scenario is StripeHostScenario.UNRELATED_RESOURCE_PROGRESS:
        second = _intent(descriptor, scenario, suffix="two", resource_suffix="two")
        await adapter.remember(first)
        await adapter.remember(second)
        results = await asyncio.gather(
            adapter.reserve(first, not_after=future),
            adapter.reserve(second, not_after=future),
        )
        _require(
            scenario,
            all(result is StripeHostReserveStatus.ACQUIRED for result in results),
            "unrelated_resource_blocked",
        )
        return

    if scenario is StripeHostScenario.NORMAL_WRITER_EXCLUSION:
        await adapter.remember(first)
        checkpoint = _NormalWriteRaceCheckpoint()
        writer_task = asyncio.create_task(
            adapter.normal_write(
                tenant_reference=first.tenant_reference,
                resource_reference=first.resource_reference,
                checkpoint=checkpoint,
            )
        )
        try:
            await asyncio.wait_for(checkpoint.wait_until_checked(), timeout=1.0)
            reserve_task = asyncio.create_task(adapter.reserve(first, not_after=future))
            completed, _ = await asyncio.wait({reserve_task}, timeout=0.1)
            if completed:
                checkpoint.release()
                await writer_task
                _require(scenario, False, "reservation_bypassed_writer_lock")
            checkpoint.release()
            normal, reserved = await asyncio.gather(writer_task, reserve_task)
        finally:
            checkpoint.release()
            if not writer_task.done():
                writer_task.cancel()
                await asyncio.gather(writer_task, return_exceptions=True)
        _require(scenario, normal is StripeHostNormalWriteStatus.APPLIED, "writer_not_applied")
        _require(scenario, reserved is StripeHostReserveStatus.STALE, "writer_drift_not_detected")
        second = _intent(descriptor, scenario, suffix="two", resource_suffix="two")
        await adapter.remember(second)
        acquired = await adapter.reserve(second, not_after=future)
        post_reservation_checkpoint = _NormalWriteRaceCheckpoint()
        post_reservation_checkpoint.release()
        refused = await adapter.normal_write(
            tenant_reference=second.tenant_reference,
            resource_reference=second.resource_reference,
            checkpoint=post_reservation_checkpoint,
        )
        _require(scenario, acquired is StripeHostReserveStatus.ACQUIRED, "reserve_failed")
        _require(scenario, refused is StripeHostNormalWriteStatus.REFUSED, "writer_not_excluded")
        return

    if scenario is StripeHostScenario.LOST_RESERVATION_ACKNOWLEDGEMENT:
        await adapter.remember(first)
        lost = await adapter.reserve(
            first,
            not_after=future,
            simulate_lost_acknowledgement=True,
        )
        observed = await adapter.load(
            tenant_reference=first.tenant_reference,
            action_group=first.action_group,
            effect_reference=first.effect_reference,
        )
        repeated = await adapter.reserve(first, not_after=future)
        _require(
            scenario,
            lost is StripeHostReserveStatus.ACKNOWLEDGEMENT_LOST,
            "fault_not_injected",
        )
        _require(
            scenario,
            observed is not None and observed.phase is StripeHostIntentPhase.RESERVED,
            "reservation_not_durable",
        )
        _require(
            scenario,
            repeated is StripeHostReserveStatus.ALREADY_SUBMITTED,
            "intent_reopened",
        )
        return

    if scenario is StripeHostScenario.EXPIRED_ADMISSION:
        await adapter.remember(first)
        expired = await adapter.reserve(first, not_after=now - timedelta(microseconds=1))
        observed = await adapter.load(
            tenant_reference=first.tenant_reference,
            action_group=first.action_group,
            effect_reference=first.effect_reference,
        )
        _require(scenario, expired is StripeHostReserveStatus.STALE, "expired_reservation_acquired")
        _require(
            scenario,
            observed is not None and observed.phase is StripeHostIntentPhase.READY,
            "expired_intent_mutated",
        )
        return

    if scenario is StripeHostScenario.CLOSED_INTENT_NON_REOPENING:
        await adapter.remember(first)
        await adapter.reserve(first, not_after=future)
        closed = await adapter.record_no_submission(first)
        repeated = await adapter.reserve(first, not_after=future)
        _require(scenario, closed is StripeHostCloseStatus.RECORDED, "close_not_recorded")
        _require(
            scenario,
            repeated is StripeHostReserveStatus.ALREADY_SUBMITTED,
            "closed_intent_reopened",
        )
        return

    if scenario is StripeHostScenario.OUTCOME_IDEMPOTENCE:
        outcome: dict[str, JsonValue] = {"status": "succeeded", "amount": "10.00"}
        changed_outcome: dict[str, JsonValue] = {"status": "failed", "amount": "10.00"}
        await adapter.remember(first)
        await adapter.reserve(first, not_after=future)
        recorded = await adapter.record_outcome(first, outcome_data=outcome)
        outcome_repeated = await adapter.record_outcome(first, outcome_data=outcome)
        outcome_conflict = await adapter.record_outcome(first, outcome_data=changed_outcome)
        observed = await adapter.load(
            tenant_reference=first.tenant_reference,
            action_group=first.action_group,
            effect_reference=first.effect_reference,
        )
        _require(scenario, recorded is StripeHostCloseStatus.RECORDED, "outcome_not_recorded")
        _require(
            scenario,
            outcome_repeated is StripeHostCloseStatus.MATCHED,
            "outcome_not_idempotent",
        )
        _require(
            scenario,
            outcome_conflict is StripeHostCloseStatus.CONFLICT,
            "outcome_rebound",
        )
        _require(
            scenario,
            observed is not None and observed.outcome_digest == _outcome_digest(outcome),
            "outcome_changed",
        )
        return

    if scenario is StripeHostScenario.DELAYED_CLOSURE_OWNER_SAFETY:
        second = _intent(
            descriptor,
            scenario,
            suffix="two",
            action_group=_other_group(first.action_group),
        )
        await adapter.remember(first)
        await adapter.remember(second)
        acquired = await adapter.reserve(first, not_after=future)
        blocked = await adapter.reserve(second, not_after=future)
        owner = await adapter.load(
            tenant_reference=first.tenant_reference,
            action_group=first.action_group,
            effect_reference=first.effect_reference,
        )
        _require(scenario, acquired is StripeHostReserveStatus.ACQUIRED, "owner_not_acquired")
        _require(scenario, blocked is StripeHostReserveStatus.UNAVAILABLE, "sibling_acquired")
        _require(
            scenario,
            owner is not None and owner.phase is StripeHostIntentPhase.RESERVED,
            "owner_not_retained",
        )
        return

    if scenario is StripeHostScenario.DIAGNOSTIC_MINIMIZATION:
        await adapter.remember(first)
        changed = first.model_copy(
            update={
                "snapshot_data": FrozenJsonObject.from_mapping(
                    {
                        **first.snapshot_mapping(),
                        "marker": "diagnostic-conflict",
                    }
                )
            }
        )
        result = await adapter.remember(changed)
        _require(scenario, result is StripeHostRememberStatus.CONFLICT, "conflict_not_minimized")
        return

    raise AssertionError(f"unhandled Stripe host scenario: {scenario.value}")


async def assert_stripe_host_exercise(
    adapter: StripeHostExerciseAdapter,
    *,
    clock: Clock | None = None,
) -> StripeHostExerciseReport:
    """Execute every scenario and return evidence only when library assertions pass."""

    resolved_clock = clock or SystemClock()
    results: list[StripeHostScenarioResult] = []
    for scenario in _REQUIRED_SCENARIOS:
        try:
            await _exercise_scenario(adapter, scenario, clock=resolved_clock)
        except StripeHostConformanceError:
            raise
        except Exception:
            raise StripeHostConformanceError(
                f"stripe_host:{scenario.value}:adapter_error"
            ) from None
        results.append(
            StripeHostScenarioResult(
                scenario=scenario,
                disposition=StripeHostScenarioDisposition.PASSED,
            )
        )
    descriptor = adapter.descriptor
    return StripeHostExerciseReport(
        action_group=descriptor.action_group,
        profile_identifier=descriptor.profile_identifier,
        results=tuple(results),
    )
