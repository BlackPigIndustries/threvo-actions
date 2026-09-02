"""Small executable tour of the experimental authoring API.

Run from the source distribution with ``uv run python -m examples.docs.quickstart``.
The imported refund host is the production-shaped part; this file stays small
so the registration, inspection, and operation flow are easy to see.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

from examples.refund.app import TENANT, build_refund_application

# --8<-- [start:models]
from examples.refund.domain import PaymentOrder, RefundCommand
from threvo_actions import EvidenceConsumer, Money, ReadContext

# --8<-- [end:models]


def money(amount: str) -> Money:
    return Money(amount=Decimal(amount), currency="EUR")


# --8<-- [start:run]
async def main() -> None:
    # --8<-- [start:definition]
    demo = build_refund_application()
    contract = demo.actions.inspect(demo.refund)
    print(contract.action_type)
    # --8<-- [end:definition]
    demo.ledger.add(
        PaymentOrder(
            order_reference="order:42",
            payment_reference="payment:private:42",
            customer_contact="private@example.test",
            captured=money("100.00"),
            refunded=money("20.00"),
        )
    )

    # --8<-- [start:preparation]
    prepared = await demo.prepare(
        RefundCommand(
            intent_reference="intent:refund:42",
            order_reference="order:42",
            amount=money("30.00"),
        )
    )
    print(prepared.display_preview)
    # --8<-- [end:preparation]

    # --8<-- [start:authority-policy]
    # The host authenticates the manager and records exact authority evidence.
    # --8<-- [end:authority-policy]
    # --8<-- [start:record-authority]
    await demo.approve(prepared.proposal_reference)
    # --8<-- [end:record-authority]

    # --8<-- [start:drift]
    # Execution re-reads the order and refuses material drift.
    # --8<-- [end:drift]
    # --8<-- [start:execute-and-verify]
    accepted = await demo.execute(prepared.proposal_reference)
    demo.clock.advance(timedelta(seconds=5))
    verified = await demo.reconcile(prepared.proposal_reference)
    print(accepted.outcome, verified.outcome, verified.safe_result)
    # --8<-- [end:execute-and-verify]

    view = await demo.read(
        prepared.proposal_reference,
        context=ReadContext(
            tenant_reference=TENANT,
            consumer=EvidenceConsumer(reference="operator:auditor"),
        ),
    )
    print([receipt.receipt_type for receipt in view.receipts])


# --8<-- [end:run]

if __name__ == "__main__":
    asyncio.run(main())
