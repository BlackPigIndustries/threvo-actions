from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import assert_type

from pydantic import BaseModel, ConfigDict

from threvo_actions.experimental import (
    ActionApplication,
    ActionComponents,
    ActionRecipe,
    ActionSpec,
    RegisteredAction,
)
from threvo_actions.models import ActionType, AuthoritativeTarget, GovernedExecutor


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Command(BoundaryModel):
    reference: str


class PrivateSnapshot(BoundaryModel):
    reference: str


class Preview(BoundaryModel):
    summary: str


class Result(BoundaryModel):
    reference: str


@dataclass(frozen=True)
class Dependencies:
    tenant_reference: str


def bind_components(
    dependencies: Dependencies,
) -> ActionComponents[Command, PrivateSnapshot, Preview, Result]:
    del dependencies
    raise NotImplementedError


specification = ActionSpec[Command, PrivateSnapshot, Preview, Result](
    command_model=Command,
    private_snapshot_model=PrivateSnapshot,
    display_preview_model=Preview,
    result_model=Result,
    action_type=ActionType(namespace="example.billing", name="refund", version=1),
    proposal_ttl=timedelta(minutes=10),
    executor_identity=GovernedExecutor(reference="service:refunds"),
    target_identity=AuthoritativeTarget(reference="psp:refunds"),
    authority_audience="service:refunds",
    authority_channel_assurance="authenticated_session",
)
recipe = ActionRecipe[Dependencies, Command, PrivateSnapshot, Preview, Result](bind=bind_components)
application = ActionApplication[Dependencies]()
registered = application.register(specification, recipe)
assert_type(
    registered,
    RegisteredAction[Command, PrivateSnapshot, Preview, Result],
)
