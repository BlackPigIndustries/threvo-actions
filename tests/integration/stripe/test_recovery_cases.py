from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from examples.stripe_host.cases import StripeRecoveryCaseService

from threvo_actions import EvidenceConsumer, ReadContext
from threvo_actions.integrations.stripe import (
    StripeCaseAcknowledgement,
    StripeEffectObservation,
    StripeRecoveryCase,
    stripe_refund_scenario,
)
from threvo_actions.integrations.stripe.conformance import StripeHostActionGroup

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


class Authorization:
    def __init__(self) -> None:
        self.allowed = True

    async def can_observe(self, *, tenant_reference: str, operator_reference: str) -> bool:
        return self.allowed and tenant_reference == "tenant:demo" and operator_reference == "ops:1"

    async def can_acknowledge(self, *, tenant_reference: str, operator_reference: str) -> bool:
        return await self.can_observe(
            tenant_reference=tenant_reference, operator_reference=operator_reference
        )


class Cases:
    def __init__(self, recovery_case: StripeRecoveryCase) -> None:
        self.recovery_case = recovery_case
        self.observations: list[StripeEffectObservation] = []
        self.acknowledgement: StripeCaseAcknowledgement | None = None

    async def load(self, tenant_reference: str, case_reference: str) -> StripeRecoveryCase | None:
        if (
            tenant_reference != self.recovery_case.tenant_reference
            or case_reference != self.recovery_case.case_reference
        ):
            return None
        return self.recovery_case.model_copy(
            update={
                "observations": tuple(self.observations),
                "acknowledgement": self.acknowledgement,
            }
        )

    async def append(
        self,
        *,
        tenant_reference: str,
        case_reference: str,
        observation_reference: str,
        observation: StripeEffectObservation,
    ) -> None:
        del observation_reference
        assert await self.load(tenant_reference, case_reference) is not None
        if observation not in self.observations:
            self.observations.append(observation)

    async def acknowledge(
        self,
        *,
        tenant_reference: str,
        case_reference: str,
        acknowledgement: StripeCaseAcknowledgement,
    ) -> bool:
        assert await self.load(tenant_reference, case_reference) is not None
        if self.acknowledgement is not None:
            return self.acknowledgement == acknowledgement
        self.acknowledgement = acknowledgement
        return True


def test_case_observation_and_acknowledgement_are_authorized_and_separate() -> None:
    async def scenario() -> None:
        demo = stripe_refund_scenario()
        prepared = await demo.prepare()
        stored = await demo.store.get("tenant:demo", prepared.proposal_reference)
        assert stored is not None
        recovery_case = StripeRecoveryCase(
            case_reference="case:1",
            tenant_reference="tenant:demo",
            proposal_reference=prepared.proposal_reference,
            semantic_effect_reference=stored.semantic_effect_reference,
            action_group=StripeHostActionGroup.REFUNDS,
            opened_at=NOW,
            retain_until=NOW + timedelta(days=30),
        )
        cases = Cases(recovery_case)
        authorization = Authorization()
        service = StripeRecoveryCaseService(
            repository=cases,
            authorization=authorization,
            observers={StripeHostActionGroup.REFUNDS: demo.actions.refunds},
        )
        read_context = ReadContext(
            tenant_reference="tenant:demo",
            consumer=EvidenceConsumer(reference="user:requester"),
        )
        observation = await service.observe(
            tenant_reference="tenant:demo",
            case_reference="case:1",
            operator_reference="ops:1",
            context=read_context,
        )
        assert cases.observations == [observation]
        assert await service.acknowledge(
            tenant_reference="tenant:demo",
            case_reference="case:1",
            operator_reference="ops:1",
            acknowledged_at=NOW,
        )
        assert cases.acknowledgement is not None
        assert await service.acknowledge(
            tenant_reference="tenant:demo",
            case_reference="case:1",
            operator_reference="ops:1",
            acknowledged_at=NOW,
        )
        assert not await service.acknowledge(
            tenant_reference="tenant:demo",
            case_reference="case:1",
            operator_reference="ops:1",
            acknowledged_at=NOW + timedelta(seconds=1),
        )
        assert demo.gateway.submissions == 0

        authorization.allowed = False
        with pytest.raises(LookupError):
            await service.observe(
                tenant_reference="tenant:demo",
                case_reference="case:1",
                operator_reference="ops:1",
                context=read_context,
            )

    asyncio.run(scenario())
