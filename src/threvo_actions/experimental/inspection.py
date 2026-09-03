"""Content-safe static inspection for experimental action declarations."""

# Pydantic resolves public projection annotations at runtime.
# ruff: noqa: TC001, TC003

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..models import ActionType, EffectKind, SafeReference


class InspectionModel(BaseModel):
    """Strict frozen base for allowlisted inspection projections."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BoundaryModelInspection(InspectionModel):
    role: Literal["command", "private_snapshot", "display_preview", "result"]
    strict: Literal[True] = True
    frozen: Literal[True] = True
    extra: Literal["forbid"] = "forbid"


class ActionSettingsInspection(InspectionModel):
    proposal_ttl: timedelta
    verification_delay: timedelta
    max_verification_attempts: int
    effect_kind: EffectKind
    allow_resend_after_final_absence: bool
    verification_lease_duration: timedelta
    semantic_idempotency_strategy: Literal["host_defined"]


class ActionOwnershipInspection(InspectionModel):
    dependency_scope: Literal["host_owned"] = "host_owned"
    resources: Literal["borrowed_per_binding"] = "borrowed_per_binding"
    transaction_coherence: Literal["host_enforced_not_verified"] = "host_enforced_not_verified"
    tenant_coherence: Literal["host_enforced_not_verified"] = "host_enforced_not_verified"
    live_readiness: Literal["not_evaluated"] = "not_evaluated"
    authorization: Literal["action_specific_fail_closed"] = "action_specific_fail_closed"


class ActionInspection(InspectionModel):
    """Allowlisted declaration projection with no live-service claims."""

    action_type: ActionType
    boundary_models: tuple[BoundaryModelInspection, ...]
    settings: ActionSettingsInspection
    source: Literal["registered_recipe"] = "registered_recipe"
    ownership: ActionOwnershipInspection = ActionOwnershipInspection()
    issue_codes: tuple[SafeReference, ...]
    catalog_frozen: bool
