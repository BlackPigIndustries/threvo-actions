from __future__ import annotations

import pytest
from pydantic import SecretStr
from tests.qualification.stripe.qualification import (
    StripeQualificationReport,
    StripeSandboxConfig,
)


def test_sandbox_config_refuses_live_keys_before_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("THREVO_ACTIONS_STRIPE_SANDBOX_API_KEY", "sk_live_forbidden")
    monkeypatch.setenv(
        "THREVO_ACTIONS_STRIPE_TEST_DSN",
        "postgresql://localhost/ta_stripe_test_qualification",
    )

    with pytest.raises(ValueError, match="refuses non-test"):
        StripeSandboxConfig.from_environment()


def test_sandbox_config_requires_both_explicit_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("THREVO_ACTIONS_STRIPE_SANDBOX_API_KEY", raising=False)
    monkeypatch.delenv("THREVO_ACTIONS_STRIPE_TEST_DSN", raising=False)

    assert StripeSandboxConfig.from_environment() is None


def test_report_model_hides_secret_inputs() -> None:
    with pytest.raises(ValueError):
        StripeQualificationReport.model_validate(
            {
                "scenario": "refund:test",
                "action_group": "refunds",
                "disposition": "refund",
                "evidence_class": "sandbox",
                "status": "passed",
                "source_revision": "source:test",
                "stripe_sdk_version": "14.4.1",
                "stripe_api_version": "2026-02-25.clover",
                "account_mode": "test",
                "database_exercised": True,
                "provider_exercised": True,
                "cleanup_status": "complete",
                "recorded_at": "2026-09-12T12:00:00Z",
                "api_key": SecretStr("sk_test_forbidden"),
            }
        )
