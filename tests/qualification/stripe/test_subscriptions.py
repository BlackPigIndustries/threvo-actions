from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest
import stripe
from tests.qualification.stripe.qualification import (
    QualificationStatus,
    StripeQualificationReport,
    StripeSandboxConfig,
    record_report,
)

from threvo_actions.integrations.stripe import (
    StripeAccount,
    StripeSubscriptionSDKGateway,
    SubscriptionBinding,
    SubscriptionCancellationPolicy,
    SubscriptionCancellationSnapshot,
    SubscriptionOperation,
)

pytestmark = pytest.mark.stripe_sandbox


def _fixture(name: str) -> str:
    value = os.environ.get(name)
    if value is None:
        pytest.skip(f"set {name} to a disposable Stripe test fixture")
    return value


async def _qualify(config: StripeSandboxConfig, operation: SubscriptionOperation) -> None:
    client = stripe.StripeClient(
        config.api_key.get_secret_value(),
        stripe_version=config.api_version,
        max_network_retries=0,
    )
    binding = SubscriptionBinding(
        tenant_reference="tenant:qualification",
        subscription_reference="subscription:qualification",
        version=1,
        account=StripeAccount(reference="merchant:qualification"),
        subscription_id=_fixture("THREVO_ACTIONS_STRIPE_SUBSCRIPTION_ID"),
        customer_id=_fixture("THREVO_ACTIONS_STRIPE_SUBSCRIPTION_CUSTOMER_ID"),
    )
    gateway = StripeSubscriptionSDKGateway(client)
    before = await gateway.retrieve(binding)
    snapshot = SubscriptionCancellationSnapshot(
        tenant_reference=binding.tenant_reference,
        intent_reference=f"qualification:{operation.value}:{datetime.now(UTC).timestamp()}",
        policy_fingerprint=SubscriptionCancellationPolicy().fingerprint,
        binding=binding,
        operation=operation,
        observed=before,
    )

    submitted = await gateway.update(snapshot)
    observed = await gateway.retrieve(binding)

    assert snapshot.matches_result(submitted)
    assert snapshot.matches_result(observed)
    record_report(
        StripeQualificationReport(
            scenario=f"subscription:{operation.value}",
            action_group="subscriptions",
            disposition=operation.value,
            evidence_class="sandbox",
            status=QualificationStatus.PASSED,
            source_revision=os.environ.get("GITHUB_SHA", "local:unrecorded"),
            stripe_sdk_version=stripe.VERSION,
            stripe_api_version=config.api_version,
            account_mode="test",
            database_exercised=False,
            provider_exercised=True,
            cleanup_status="fixture_state_changed_as_requested",
            recorded_at=datetime.now(UTC),
        )
    )


def test_subscription_schedule_and_withdraw_in_sandbox(
    sandbox_config: StripeSandboxConfig,
) -> None:
    async def scenario() -> None:
        await _qualify(sandbox_config, SubscriptionOperation.SCHEDULE)
        await _qualify(sandbox_config, SubscriptionOperation.WITHDRAW)

    asyncio.run(scenario())
