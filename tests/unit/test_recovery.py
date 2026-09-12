from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from threvo_actions.models import LifecycleStatus
from threvo_actions.receipts import VerificationReceiptStatus
from threvo_actions.recovery import (
    ActionEffectOwnership,
    ActionRecoveryCondition,
    ActionRecoveryOperation,
    ActionRecoveryView,
    recovery_condition,
    recovery_steps,
)

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("status", "condition"),
    [
        (LifecycleStatus.AWAITING_AUTHORITY, ActionRecoveryCondition.WAITING_FOR_AUTHORITY),
        (LifecycleStatus.AUTHORIZED, ActionRecoveryCondition.READY_FOR_EXECUTION),
        (LifecycleStatus.EXECUTING, ActionRecoveryCondition.OUTCOME_UNPROVEN),
        (LifecycleStatus.FAILED_UNKNOWN, ActionRecoveryCondition.OUTCOME_UNPROVEN),
        (LifecycleStatus.VERIFICATION_PENDING, ActionRecoveryCondition.OUTCOME_UNPROVEN),
        (LifecycleStatus.VERIFICATION_UNRESOLVED, ActionRecoveryCondition.OPERATOR_ATTENTION),
        (LifecycleStatus.PARTIALLY_SUCCEEDED, ActionRecoveryCondition.OPERATOR_ATTENTION),
        (LifecycleStatus.VERIFIED, ActionRecoveryCondition.RESOLVED),
        (LifecycleStatus.FAILED_KNOWN, ActionRecoveryCondition.RESOLVED),
        (LifecycleStatus.STALE, ActionRecoveryCondition.REPLACEMENT_REQUIRED),
        (LifecycleStatus.SUPERSEDED, ActionRecoveryCondition.REPLACEMENT_REQUIRED),
        (LifecycleStatus.DENIED, ActionRecoveryCondition.REFUSED),
        (LifecycleStatus.BLOCKED, ActionRecoveryCondition.REFUSED),
        (LifecycleStatus.EXPIRED, ActionRecoveryCondition.REFUSED),
    ],
)
def test_recovery_condition_maps_every_lifecycle(
    status: LifecycleStatus, condition: ActionRecoveryCondition
) -> None:
    assert (
        recovery_condition(
            lifecycle_status=status,
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            next_verification_at=None,
            last_verification_status=None,
        )
        is condition
    )


def test_recovery_condition_uses_exact_deadline_and_observation_semantics() -> None:
    assert (
        recovery_condition(
            lifecycle_status=LifecycleStatus.AUTHORIZED,
            observed_at=NOW,
            expires_at=NOW,
            next_verification_at=None,
            last_verification_status=None,
        )
        is ActionRecoveryCondition.EXPIRY_DUE
    )
    assert (
        recovery_condition(
            lifecycle_status=LifecycleStatus.EXECUTING,
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            next_verification_at=NOW + timedelta(seconds=1),
            last_verification_status=None,
        )
        is ActionRecoveryCondition.ACTIVE_EXECUTION_LEASE
    )
    assert (
        recovery_condition(
            lifecycle_status=LifecycleStatus.VERIFICATION_PENDING,
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            next_verification_at=NOW,
            last_verification_status=VerificationReceiptStatus.TARGET_UNAVAILABLE,
        )
        is ActionRecoveryCondition.OBSERVATION_UNAVAILABLE
    )
    assert (
        recovery_condition(
            lifecycle_status=LifecycleStatus.VERIFICATION_PENDING,
            observed_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            next_verification_at=NOW,
            last_verification_status=VerificationReceiptStatus.PROVISIONAL_ABSENCE,
        )
        is ActionRecoveryCondition.WAITING_FOR_PROVIDER
    )


def test_recovery_steps_are_advisory_and_strict() -> None:
    step = recovery_steps(
        condition=ActionRecoveryCondition.READY_FOR_EXECUTION,
        next_verification_at=None,
        reason_code=None,
    )
    assert step[0].operation is ActionRecoveryOperation.EXECUTE
    with pytest.raises(ValidationError):
        ActionRecoveryView.model_validate(
            {
                "proposal_reference": "proposal:test",
                "lifecycle_status": "authorized",
                "revision": 0,
                "erased": False,
                "observed_at": NOW.isoformat(),
                "condition": "ready_for_execution",
                "effect_ownership": ActionEffectOwnership.UNCLAIMED,
                "authority_token": "forbidden",
            }
        )
