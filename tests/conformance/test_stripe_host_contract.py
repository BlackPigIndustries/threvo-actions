from __future__ import annotations

import asyncio

import pytest

from threvo_actions.integrations.stripe import (
    StripeHostActionGroup,
    StripeHostCapabilities,
    StripeHostConformanceDescriptor,
    StripeHostConformanceError,
    StripeHostScenario,
    StripeHostScenarioDisposition,
    StripeHostScenarioResult,
    assert_stripe_host_conforms,
)


class Driver:
    def __init__(
        self,
        *,
        capabilities: StripeHostCapabilities | None = None,
        failure: StripeHostScenario | None = None,
        disposition: StripeHostScenarioDisposition = StripeHostScenarioDisposition.FAILED,
        raises: StripeHostScenario | None = None,
        mismatch: StripeHostScenario | None = None,
        action_group: StripeHostActionGroup = StripeHostActionGroup.REFUNDS,
    ) -> None:
        self._descriptor = StripeHostConformanceDescriptor(
            action_group=action_group,
            profile_identifier=f"postgres:{action_group.value}:test",
            capabilities=capabilities
            or StripeHostCapabilities(
                independent_connections=True,
                normal_application_writer=True,
                lost_acknowledgement_injection=True,
            ),
        )
        self.failure = failure
        self.disposition = disposition
        self.raises = raises
        self.mismatch = mismatch
        self.seen: list[StripeHostScenario] = []

    @property
    def descriptor(self) -> StripeHostConformanceDescriptor:
        return self._descriptor

    async def run_scenario(self, scenario: StripeHostScenario) -> StripeHostScenarioResult:
        self.seen.append(scenario)
        if scenario is self.raises:
            raise RuntimeError("database-secret-value private payload")
        if scenario is self.mismatch:
            return StripeHostScenarioResult(
                scenario=StripeHostScenario.TENANT_ISOLATION,
                disposition=StripeHostScenarioDisposition.PASSED,
            )
        if scenario is self.failure:
            return StripeHostScenarioResult(
                scenario=scenario,
                disposition=self.disposition,
                reason_code="fixture:expected_failure",
            )
        return StripeHostScenarioResult(
            scenario=scenario,
            disposition=StripeHostScenarioDisposition.PASSED,
        )


@pytest.mark.parametrize("action_group", list(StripeHostActionGroup))
def test_complete_group_driver_returns_strict_passing_report(
    action_group: StripeHostActionGroup,
) -> None:
    driver = Driver(action_group=action_group)

    report = asyncio.run(assert_stripe_host_conforms(driver))

    assert report.passed is True
    assert report.action_group is action_group
    assert report.schema_version == "stripe-host-conformance/v1"
    assert tuple(result.scenario for result in report.results) == tuple(StripeHostScenario)
    assert driver.seen == list(StripeHostScenario)


@pytest.mark.parametrize(
    "capability",
    [
        "independent_connections",
        "normal_application_writer",
        "lost_acknowledgement_injection",
    ],
)
def test_missing_required_capability_cannot_produce_a_passing_report(capability: str) -> None:
    values = {
        "independent_connections": True,
        "normal_application_writer": True,
        "lost_acknowledgement_injection": True,
    }
    values[capability] = False
    driver = Driver(capabilities=StripeHostCapabilities.model_validate(values))

    with pytest.raises(StripeHostConformanceError) as caught:
        asyncio.run(assert_stripe_host_conforms(driver))

    assert caught.value.code == f"stripe_host:capability:{capability}:not_exercised"
    assert driver.seen == []


@pytest.mark.parametrize(
    "disposition",
    [StripeHostScenarioDisposition.FAILED, StripeHostScenarioDisposition.NOT_EXERCISED],
)
def test_broken_or_unexercised_scenario_fails_closed(
    disposition: StripeHostScenarioDisposition,
) -> None:
    driver = Driver(
        failure=StripeHostScenario.SAME_EFFECT_RACE,
        disposition=disposition,
    )

    with pytest.raises(StripeHostConformanceError) as caught:
        asyncio.run(assert_stripe_host_conforms(driver))

    assert caught.value.code == f"stripe_host:same_effect_race:{disposition.value}"


def test_driver_exception_is_sanitized() -> None:
    driver = Driver(raises=StripeHostScenario.LOST_RESERVATION_ACKNOWLEDGEMENT)

    with pytest.raises(StripeHostConformanceError) as caught:
        asyncio.run(assert_stripe_host_conforms(driver))

    assert caught.value.code == "stripe_host:lost_reservation_acknowledgement:driver_error"
    assert "database-secret-value" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_driver_cancellation_is_not_converted_to_a_conformance_failure() -> None:
    class CancelledDriver(Driver):
        async def run_scenario(self, scenario: StripeHostScenario) -> StripeHostScenarioResult:
            del scenario
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(assert_stripe_host_conforms(CancelledDriver()))


def test_mismatched_scenario_result_is_rejected() -> None:
    driver = Driver(mismatch=StripeHostScenario.SAME_EFFECT_RACE)

    with pytest.raises(StripeHostConformanceError) as caught:
        asyncio.run(assert_stripe_host_conforms(driver))

    assert caught.value.code == "stripe_host:same_effect_race:mismatched_result"


def test_scenario_result_validation_prevents_ambiguous_evidence() -> None:
    with pytest.raises(ValueError):
        StripeHostScenarioResult(
            scenario=StripeHostScenario.IMMUTABLE_INTENT,
            disposition=StripeHostScenarioDisposition.FAILED,
        )
    with pytest.raises(ValueError):
        StripeHostScenarioResult(
            scenario=StripeHostScenario.IMMUTABLE_INTENT,
            disposition=StripeHostScenarioDisposition.PASSED,
            reason_code="unexpected",
        )
