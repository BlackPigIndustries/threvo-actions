from __future__ import annotations

import os

import pytest

stripe = pytest.importorskip("stripe")
pytest.importorskip("asyncpg")

from tests.qualification.stripe.qualification import (  # noqa: E402
    StripeSandboxConfig,
    not_exercised_report,
    record_report,
)


@pytest.fixture
def sandbox_config() -> StripeSandboxConfig:
    scenario = os.environ.get("THREVO_ACTIONS_STRIPE_SCENARIO", "refund:preflight")
    source_revision = os.environ.get("GITHUB_SHA", "local:unrecorded")
    config = StripeSandboxConfig.from_environment()
    if config is None:
        record_report(
            not_exercised_report(
                scenario=scenario,
                source_revision=source_revision,
                stripe_sdk_version=stripe.VERSION,
            )
        )
        reason = "set THREVO_ACTIONS_STRIPE_SANDBOX_API_KEY and THREVO_ACTIONS_STRIPE_TEST_DSN"
        if os.environ.get("THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX") == "1":
            pytest.fail(f"THREVO_ACTIONS_REQUIRE_STRIPE_SANDBOX=1: {reason}", pytrace=False)
        pytest.skip(reason)
    return config
