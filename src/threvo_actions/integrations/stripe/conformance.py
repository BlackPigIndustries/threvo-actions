"""Schema-independent conformance evidence for adopter-owned Stripe hosts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import Field, model_validator

from ...models import ExperimentalModel, SafeReference


class StripeHostActionGroup(StrEnum):
    """Repository contract exercised by a conformance driver."""

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


class StripeHostCapabilities(ExperimentalModel):
    """Environmental capabilities that make the result meaningful."""

    independent_connections: bool
    normal_application_writer: bool
    lost_acknowledgement_injection: bool


class StripeHostConformanceDescriptor(ExperimentalModel):
    """Identity and capabilities declared by an adopter fixture."""

    action_group: StripeHostActionGroup
    profile_identifier: SafeReference
    capabilities: StripeHostCapabilities


class StripeHostScenarioResult(ExperimentalModel):
    """Secret-free result returned by one driver scenario."""

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


class StripeHostConformanceReport(ExperimentalModel):
    """Deterministic evidence emitted only after every required check passes."""

    schema_version: Literal["stripe-host-conformance/v1"] = "stripe-host-conformance/v1"
    action_group: StripeHostActionGroup
    profile_identifier: SafeReference
    capabilities: StripeHostCapabilities
    results: tuple[StripeHostScenarioResult, ...] = Field(min_length=1)

    @property
    def passed(self) -> bool:
        return all(
            result.disposition is StripeHostScenarioDisposition.PASSED
            for result in self.results
        )


class StripeHostConformanceError(AssertionError):
    """Stable conformance failure that never includes driver exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class StripeHostConformanceDriver(Protocol):
    """Adopter fixture that executes scenarios against its real repository."""

    @property
    def descriptor(self) -> StripeHostConformanceDescriptor: ...

    async def run_scenario(
        self, scenario: StripeHostScenario
    ) -> StripeHostScenarioResult: ...


_REQUIRED_SCENARIOS = tuple(StripeHostScenario)


def _require_capabilities(descriptor: StripeHostConformanceDescriptor) -> None:
    capabilities = descriptor.capabilities
    missing = (
        ("independent_connections", not capabilities.independent_connections),
        ("normal_application_writer", not capabilities.normal_application_writer),
        (
            "lost_acknowledgement_injection",
            not capabilities.lost_acknowledgement_injection,
        ),
    )
    for name, absent in missing:
        if absent:
            raise StripeHostConformanceError(f"stripe_host:capability:{name}:not_exercised")


async def assert_stripe_host_conforms(
    driver: StripeHostConformanceDriver,
) -> StripeHostConformanceReport:
    """Run every v1 scenario and return evidence only for a passing fixture."""

    descriptor = driver.descriptor
    _require_capabilities(descriptor)
    results: list[StripeHostScenarioResult] = []
    for scenario in _REQUIRED_SCENARIOS:
        try:
            result = await driver.run_scenario(scenario)
        except Exception:
            raise StripeHostConformanceError(
                f"stripe_host:{scenario.value}:driver_error"
            ) from None
        if result.scenario is not scenario:
            raise StripeHostConformanceError(
                f"stripe_host:{scenario.value}:mismatched_result"
            )
        if result.disposition is not StripeHostScenarioDisposition.PASSED:
            raise StripeHostConformanceError(
                f"stripe_host:{scenario.value}:{result.disposition.value}"
            )
        results.append(result)
    report = StripeHostConformanceReport(
        action_group=descriptor.action_group,
        profile_identifier=descriptor.profile_identifier,
        capabilities=descriptor.capabilities,
        results=tuple(results),
    )
    return report
