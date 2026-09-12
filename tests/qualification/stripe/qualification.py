"""Safe report models for opt-in Stripe sandbox qualification."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, SecretStr

from threvo_actions.models import ExperimentalModel, SafeReference

QUALIFIED_API_VERSION = "2026-02-25.clover"


class QualificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_EXERCISED = "not_exercised"


class StripeSandboxConfig(ExperimentalModel):
    api_key: SecretStr
    database_url: SecretStr
    api_version: SafeReference = QUALIFIED_API_VERSION

    @classmethod
    def from_environment(cls) -> StripeSandboxConfig | None:
        api_key = os.environ.get("THREVO_ACTIONS_STRIPE_SANDBOX_API_KEY")
        database_url = os.environ.get("THREVO_ACTIONS_STRIPE_TEST_DSN")
        if not api_key or not database_url:
            return None
        if not api_key.startswith(("sk_test_", "rk_test_")):
            raise ValueError("Stripe sandbox qualification refuses non-test credentials")
        return cls(
            api_key=SecretStr(api_key),
            database_url=SecretStr(database_url),
        )


class StripeQualificationReport(ExperimentalModel):
    schema_version: Literal["threvo.actions.stripe-qualification/v1"] = (
        "threvo.actions.stripe-qualification/v1"
    )
    scenario: SafeReference
    action_group: SafeReference
    disposition: SafeReference
    evidence_class: SafeReference
    status: QualificationStatus
    source_revision: SafeReference
    stripe_sdk_version: SafeReference
    stripe_api_version: SafeReference
    account_mode: SafeReference
    database_exercised: bool
    provider_exercised: bool
    cleanup_status: SafeReference
    recorded_at: AwareDatetime
    reason_code: SafeReference | None = None


def record_report(report: StripeQualificationReport) -> None:
    output = os.environ.get("THREVO_ACTIONS_QUALIFICATION_REPORT_DIR")
    if output is None:
        return
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = report.scenario.replace(":", "_")
    (directory / f"{safe_name}.json").write_text(
        json.dumps(report.model_dump(mode="json"), indent=2) + "\n"
    )


def not_exercised_report(
    *, scenario: str, source_revision: str, stripe_sdk_version: str
) -> StripeQualificationReport:
    return StripeQualificationReport(
        scenario=scenario,
        action_group="refunds",
        disposition="refund",
        evidence_class="sandbox",
        status=QualificationStatus.NOT_EXERCISED,
        source_revision=source_revision,
        stripe_sdk_version=stripe_sdk_version,
        stripe_api_version=QUALIFIED_API_VERSION,
        account_mode="test",
        database_exercised=False,
        provider_exercised=False,
        cleanup_status="not_required",
        recorded_at=datetime.now(UTC),
        reason_code="sandbox_credentials_or_database_unavailable",
    )
