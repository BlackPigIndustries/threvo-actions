from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from examples.stripe_host.worker import (
    RecoveryWorker,
    RecoveryWorkerDisposition,
)

from threvo_actions import EvidenceConsumer, ReadContext
from threvo_actions.integrations.stripe import StripeRefundScenario, stripe_refund_scenario
from threvo_actions.recovery import (
    ActionWorkCursor,
    ActionWorkItem,
    ActionWorkOperation,
    ActionWorkPage,
)
from threvo_actions.testing import FixedClock

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


class StaticSource:
    def __init__(self, items: tuple[ActionWorkItem, ...]) -> None:
        self.items = items

    async def discover_due(
        self,
        *,
        tenant_reference: str,
        cutoff: datetime,
        limit: int,
        cursor: ActionWorkCursor | None = None,
    ) -> ActionWorkPage:
        del cursor
        return ActionWorkPage(
            tenant_reference=tenant_reference,
            cutoff=cutoff,
            items=self.items[:limit],
        )


class MemorySchedule:
    def __init__(self) -> None:
        self.tokens: dict[str, tuple[str, datetime]] = {}
        self.sequence = 0

    async def claim(
        self, item: ActionWorkItem, *, now: datetime, lease_until: datetime
    ) -> str | None:
        current = self.tokens.get(item.proposal_reference)
        if current is not None and current[1] > now:
            return None
        self.sequence += 1
        token = f"lease:{self.sequence}"
        self.tokens[item.proposal_reference] = (token, lease_until)
        return token

    async def complete(
        self,
        *,
        proposal_reference: str,
        token: str,
        next_attempt_at: datetime | None,
        attention_reason: str | None,
    ) -> bool:
        del attention_reason
        current = self.tokens.get(proposal_reference)
        if current is None or current[0] != token:
            return False
        if next_attempt_at is None:
            del self.tokens[proposal_reference]
        else:
            self.tokens[proposal_reference] = (token, next_attempt_at)
        return True


def work_item(
    demo: StripeRefundScenario, proposal: str, operation: ActionWorkOperation
) -> ActionWorkItem:
    action_type = demo.actions.refunds.definition.action_type
    return ActionWorkItem(
        action_type=action_type,
        proposal_reference=proposal,
        operation=operation,
        due_at=NOW,
    )


def worker_for(
    demo: StripeRefundScenario, source: StaticSource, schedule: MemorySchedule
) -> RecoveryWorker:
    group = demo.actions.refunds
    action = group.definition.action_type
    return RecoveryWorker(
        source=source,
        schedule=schedule,
        actions={(action.namespace, action.name, action.version): group},
        read_context=ReadContext(
            tenant_reference="tenant:demo",
            consumer=EvidenceConsumer(reference="user:requester"),
        ),
    )


def test_worker_recovers_lost_authorized_and_unknown_work_once() -> None:
    async def scenario() -> None:
        demo = stripe_refund_scenario(clock=FixedClock(NOW))
        prepared = await demo.prepare()
        await demo.approve(prepared.proposal_reference)
        demo.gateway.lose_response = True
        schedule = MemorySchedule()
        execute_source = StaticSource(
            (work_item(demo, prepared.proposal_reference, ActionWorkOperation.EXECUTE),)
        )
        first = await worker_for(demo, execute_source, schedule).scan(cutoff=NOW)
        assert first[0].disposition is RecoveryWorkerDisposition.COMPLETED
        assert demo.gateway.submissions == 1

        reconcile_source = StaticSource(
            (work_item(demo, prepared.proposal_reference, ActionWorkOperation.RECONCILE),)
        )
        second = await worker_for(demo, reconcile_source, schedule).scan(cutoff=NOW)
        assert second[0].disposition is RecoveryWorkerDisposition.COMPLETED
        assert demo.gateway.submissions == 1

    asyncio.run(scenario())


def test_worker_expires_old_work_and_defers_sibling_owner() -> None:
    async def scenario() -> None:
        clock = FixedClock(NOW)
        demo = stripe_refund_scenario(clock=clock)
        owner = await demo.prepare()
        await demo.approve(owner.proposal_reference)
        loser = await demo.prepare()
        await demo.approve(loser.proposal_reference)
        await demo.actions.refunds.execute(
            tenant_reference="tenant:demo", proposal_reference=owner.proposal_reference
        )
        schedule = MemorySchedule()
        result = await worker_for(
            demo,
            StaticSource(
                (work_item(demo, loser.proposal_reference, ActionWorkOperation.EXECUTE),)
            ),
            schedule,
        ).scan(cutoff=NOW)
        assert result[0].disposition is RecoveryWorkerDisposition.DEFERRED
        assert demo.gateway.submissions == 1

        fresh = stripe_refund_scenario(clock=clock)
        expired = await fresh.prepare()
        await fresh.approve(expired.proposal_reference)
        clock.advance(timedelta(minutes=11))
        expired_result = await worker_for(
            fresh,
            StaticSource(
                (work_item(fresh, expired.proposal_reference, ActionWorkOperation.EXPIRE),)
            ),
            MemorySchedule(),
        ).scan(cutoff=clock.now())
        assert expired_result[0].disposition is RecoveryWorkerDisposition.COMPLETED

    asyncio.run(scenario())


def test_stale_schedule_acknowledgement_cannot_clear_newer_lease() -> None:
    async def scenario() -> None:
        demo = stripe_refund_scenario()
        item = work_item(demo, "proposal:test", ActionWorkOperation.RECONCILE)
        schedule = MemorySchedule()
        old = await schedule.claim(item, now=NOW, lease_until=NOW + timedelta(seconds=1))
        assert old is not None
        new = await schedule.claim(
            item,
            now=NOW + timedelta(seconds=2),
            lease_until=NOW + timedelta(minutes=1),
        )
        assert new is not None and new != old
        assert not await schedule.complete(
            proposal_reference=item.proposal_reference,
            token=old,
            next_attempt_at=None,
            attention_reason=None,
        )
        assert schedule.tokens[item.proposal_reference][0] == new

    asyncio.run(scenario())


def test_unknown_action_version_is_deferred_for_attention() -> None:
    async def scenario() -> None:
        demo = stripe_refund_scenario()
        item = work_item(demo, "proposal:unknown", ActionWorkOperation.EXECUTE)
        item = item.model_copy(
            update={"action_type": item.action_type.model_copy(update={"version": 999})}
        )
        result = await worker_for(
            demo, StaticSource((item,)), MemorySchedule()
        ).scan(cutoff=NOW)
        assert result[0].disposition is RecoveryWorkerDisposition.ATTENTION
        assert result[0].reason_code == "recovery_action_unconfigured"

    asyncio.run(scenario())
