"""Print the governed merchant-apply lifecycle without credentials."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

from .app import build_application


async def main() -> None:
    application = build_application()
    application.catalog.stage_price_update(
        change_reference="change:price-planter",
        listing_reference="listing:planter",
        after_price=Decimal("105.00"),
    )
    prepared = await application.prepare("change:price-planter")
    authorized = await application.approve(prepared.proposal_reference)
    accepted = await application.execute(prepared.proposal_reference)
    application.clock.advance(timedelta(seconds=1))
    verified = await application.reconcile(prepared.proposal_reference)
    for result in (prepared, authorized, accepted, verified):
        print(result.outcome.value, result.reason_code, result.safe_result)


if __name__ == "__main__":
    asyncio.run(main())
