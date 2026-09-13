# ruff: noqa: S101
"""Executable merchant-apply failure semantics."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

from threvo_actions import OperationOutcome
from threvo_actions.recovery import ActionRecoveryOperation

from .app import MerchantApplyApplication, build_application, semantic_change_reference

CHANGE = "change:price-planter"


def _stage(application: MerchantApplyApplication) -> None:
    application.catalog.stage_price_update(
        change_reference=CHANGE,
        listing_reference="listing:planter",
        after_price=Decimal("105.00"),
    )


def test_price_drift_after_approval_refuses_the_effect() -> None:
    async def scenario() -> None:
        application = build_application()
        _stage(application)
        prepared = await application.prepare(CHANGE)
        await application.approve(prepared.proposal_reference)
        application.catalog.mutate_price("listing:planter", Decimal("101.00"))

        stale = await application.execute(prepared.proposal_reference)

        assert stale.outcome is OperationOutcome.STALE
        assert stale.reason_code == "material_drift"
        assert application.catalog.write_count == 0
        assert application.catalog.product("listing:planter").price == Decimal("101.00")

    asyncio.run(scenario())


def test_accepted_write_can_remain_pending_without_execute_advice() -> None:
    async def scenario() -> None:
        application = build_application()
        _stage(application)
        effect = semantic_change_reference(CHANGE)
        application.catalog.hide_effect_for_queries(effect, 1)
        prepared = await application.prepare(CHANGE)
        await application.approve(prepared.proposal_reference)

        accepted = await application.execute(prepared.proposal_reference)
        application.clock.advance(timedelta(seconds=1))
        pending = await application.reconcile(prepared.proposal_reference)
        recovery = await application.recovery(prepared.proposal_reference)

        assert accepted.outcome is OperationOutcome.VERIFICATION_PENDING
        assert pending.outcome is OperationOutcome.VERIFICATION_PENDING
        assert application.catalog.write_count == 1
        assert all(
            step.operation is not ActionRecoveryOperation.EXECUTE
            for step in recovery.recommended_steps
        )

        application.clock.advance(timedelta(seconds=1))
        verified = await application.reconcile(prepared.proposal_reference)
        assert verified.outcome is OperationOutcome.VERIFIED
        assert application.catalog.write_count == 1

    asyncio.run(scenario())


def test_two_portal_proposals_admit_one_catalog_write() -> None:
    async def scenario() -> None:
        application = build_application()
        _stage(application)
        prepared = await asyncio.gather(
            application.prepare(CHANGE),
            application.prepare(CHANGE),
        )
        await asyncio.gather(*(application.approve(item.proposal_reference) for item in prepared))
        application.host.execution_barrier = asyncio.Barrier(2)

        outcomes = await asyncio.gather(
            *(application.execute(item.proposal_reference) for item in prepared)
        )

        assert sum(item.outcome is OperationOutcome.VERIFICATION_PENDING for item in outcomes) == 1
        assert sum(item.outcome is OperationOutcome.REPLAYED for item in outcomes) == 1
        assert application.catalog.write_count == 1
        assert application.catalog.product("listing:planter").price == Decimal("105.00")

    asyncio.run(scenario())
