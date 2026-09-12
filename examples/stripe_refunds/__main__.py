from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg
import stripe
import uvicorn
from pydantic import SecretStr

from threvo_actions import Money
from threvo_actions.integrations.stripe import (
    StripeAccount,
    StripeBoundaryError,
    StripeRefundConnector,
    StripeSDKGateway,
)
from threvo_actions.migrations import migrate_postgres

from .models import AppError, Identity, Order, RefundCommand, Settings
from .service import RefundService
from .web import create_app

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def configure(path: Path) -> None:
    settings = Settings(
        database_url=SecretStr(os.environ["STRIPE_APP_DATABASE_URL"]),
        stripe_secret_key=SecretStr(os.environ["STRIPE_SECRET_KEY"]),
        webhook_secret=SecretStr(os.environ.get("STRIPE_WEBHOOK_SECRET", "whsec_not_configured")),
        master_key=SecretStr(secrets.token_hex(32)),
        identities=(
            Identity(
                tenant_reference="tenant:demo",
                reference="user:requester",
                role="requester",
                token=SecretStr(secrets.token_urlsafe(32)),
            ),
            Identity(
                tenant_reference="tenant:demo",
                reference="user:approver",
                role="approver",
                token=SecretStr(secrets.token_urlsafe(32)),
            ),
        ),
        refund_limits=(Money(amount=Decimal("10000.00"), currency="USD"),),
        model=os.environ.get("STRIPE_APP_MODEL"),
    )
    document = settings.model_dump(mode="json")
    for field in ("database_url", "stripe_secret_key", "webhook_secret", "master_key"):
        document[field] = getattr(settings, field).get_secret_value()
    document["identities"] = [
        {**identity.model_dump(mode="json"), "token": identity.token.get_secret_value()}
        for identity in settings.identities
    ]
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(json.dumps(document, indent=2) + "\n")
    print(json.dumps({"configured": str(path), "mode": "sandbox"}))


async def sandbox_smoke(service: RefundService, client: stripe.StripeClient) -> None:
    requester = next(i for i in service.settings.identities if i.role == "requester")
    approver = next(
        i
        for i in service.settings.identities
        if i.role == "approver" and i.tenant_reference == requester.tenant_reference
    )
    suffix = secrets.token_hex(8)
    payment = await client.v1.payment_intents.create_async(
        {
            "amount": 2000,
            "currency": "usd",
            "payment_method": "pm_card_visa",
            "payment_method_types": ["card"],
            "confirm": True,
            "metadata": {"reference_app_test": suffix},
        },
        options={"idempotency_key": f"threvo-reference-payment-{suffix}"},
    )
    charge_id = payment.latest_charge
    if not isinstance(charge_id, str) or payment.livemode:
        raise AppError("sandbox payment did not produce a test charge")
    await service.repository.add_order(
        Order(
            tenant_reference=requester.tenant_reference,
            order_reference=f"order:{suffix}",
            account=StripeAccount(reference="merchant:sandbox"),
            charge_id=charge_id,
            currency="USD",
            currency_exponent=2,
        )
    )
    proposal = await service.prepare(
        requester,
        RefundCommand(
            intent_reference=f"intent:{suffix}",
            order_reference=f"order:{suffix}",
            amount=Money(amount=Decimal("2.00"), currency="USD"),
        ),
    )
    await service.decide(approver, proposal.proposal_reference, True)
    await service.sweep()
    await asyncio.sleep(11)
    await service.sweep()
    view = await service.read(requester, proposal.proposal_reference)
    if view.lifecycle_status.value != "verified":
        raise AppError("sandbox refund has not reached verified completion")
    print(
        json.dumps(
            {
                "sandbox_refund": "verified",
                "amount": "2.00",
                "currency": "USD",
                "observed_at": datetime.now(UTC).isoformat(),
            }
        )
    )


async def run(args: argparse.Namespace) -> None:
    path = Path(args.config)
    if args.command == "configure":
        configure(path)
        return
    settings = Settings.model_validate_json(path.read_text())
    pool: asyncpg.Pool[asyncpg.Record] = await asyncpg.create_pool(
        settings.database_url.get_secret_value(),
        min_size=2,
        max_size=10,
        command_timeout=20,
    )
    transport = stripe.HTTPXClient(timeout=10)
    close_transport: Callable[[], Awaitable[None]] = transport.close_async
    client = stripe.StripeClient(
        settings.stripe_secret_key.get_secret_value(), http_client=transport, max_network_retries=0
    )
    try:
        if args.command == "init":
            await migrate_postgres(pool, schema="threvo_actions")
            await pool.execute(Path(__file__).with_name("schema.sql").read_text())
            print(json.dumps({"initialized": True}))
            return
        service = RefundService(pool, settings, StripeRefundConnector(StripeSDKGateway(client)))
        if args.command == "serve":
            await uvicorn.Server(
                uvicorn.Config(create_app(service), host="127.0.0.1", port=args.port)
            ).serve()
        elif args.command == "sweep":
            print(json.dumps({"processed": await service.sweep()}))
        elif args.command == "seed":
            await service.repository.add_order(
                Order.model_validate_json(Path(args.order).read_text())
            )
            print(json.dumps({"seeded": True}))
        elif args.command == "sandbox-smoke":
            await sandbox_smoke(service, client)
    finally:
        await close_transport()
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sandbox Stripe refund operations reference app")
    parser.add_argument("--config", default=".env.stripe-refunds.json")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("configure", "init", "sweep", "sandbox-smoke"):
        commands.add_parser(name)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8088)
    seed = commands.add_parser("seed")
    seed.add_argument("order", help="Path to a private, host-owned order JSON file")
    try:
        asyncio.run(run(parser.parse_args()))
    except (AppError, StripeBoundaryError, stripe.StripeError):
        parser.exit(1, "Operation failed; inspect the sandbox account and local configuration.\n")


if __name__ == "__main__":
    main()
