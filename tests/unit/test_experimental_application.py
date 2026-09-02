from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timedelta
from typing import get_type_hints

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from threvo_actions.experimental import (
    ActionApplication,
    ActionApplicationError,
    ActionComponents,
    ActionIssueCode,
    ActionRecipe,
    ActionSpec,
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


class MutableCommand(Command):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=False)


class FloatSnapshot(PrivateSnapshot):
    amount: float


class Dependencies:
    pass


def _unreachable_bind(
    dependencies: Dependencies,
) -> ActionComponents[Command, PrivateSnapshot, Preview, Result]:
    del dependencies
    raise AssertionError("registration must not bind live dependencies")


def specification(
    **updates: object,
) -> ActionSpec[Command, PrivateSnapshot, Preview, Result]:
    values: dict[str, object] = {
        "action_type": ActionType(namespace="example.billing", name="refund", version=1),
        "command_model": Command,
        "private_snapshot_model": PrivateSnapshot,
        "display_preview_model": Preview,
        "result_model": Result,
        "proposal_ttl": timedelta(minutes=10),
        "executor_identity": GovernedExecutor(reference="service:refunds"),
        "target_identity": AuthoritativeTarget(reference="psp:refunds"),
        "authority_audience": "service:refunds",
        "authority_channel_assurance": "authenticated_session",
    }
    values.update(updates)
    return ActionSpec[Command, PrivateSnapshot, Preview, Result].model_validate(values)


def recipe() -> ActionRecipe[Dependencies, Command, PrivateSnapshot, Preview, Result]:
    return ActionRecipe(bind=_unreachable_bind)


def test_registration_preserves_static_contract_without_binding_dependencies() -> None:
    application = ActionApplication[Dependencies]()

    registered = application.register(specification(), recipe())

    assert registered.action_type == ActionType(
        namespace="example.billing", name="refund", version=1
    )
    assert not hasattr(registered, "recipe")
    assert not hasattr(registered, "dependencies")
    assert application.registration_count == 1


def test_specification_is_frozen_and_contains_only_immutable_safety_semantics() -> None:
    declared = specification()

    with pytest.raises(ValidationError, match="frozen"):
        declared.proposal_ttl = timedelta(minutes=20)

    assert all(not isinstance(value, (dict, list, set)) for value in declared.__dict__.values())


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        ({"proposal_ttl": timedelta(0)}, "proposal_ttl"),
        ({"verification_delay": timedelta(seconds=-1)}, "verification_delay"),
        ({"max_verification_attempts": 0}, "max_verification_attempts"),
        ({"verification_lease_duration": timedelta(0)}, "verification_lease_duration"),
        ({"authority_audience": " unsafe"}, "authority_audience"),
    ),
)
def test_specification_rejects_invalid_static_semantics(
    updates: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        specification(**updates)


def test_specification_rejects_nonconforming_boundary_models() -> None:
    with pytest.raises(ValidationError, match="command model.*frozen=True"):
        specification(command_model=MutableCommand)

    with pytest.raises(ValidationError, match="floating-point values at amount"):
        specification(private_snapshot_model=FloatSnapshot)


def test_duplicate_registration_is_atomic_and_uses_a_stable_issue_code() -> None:
    application = ActionApplication[Dependencies]()
    first = application.register(specification(), recipe())

    with pytest.raises(ActionApplicationError) as captured:
        application.register(specification(), recipe())

    assert captured.value.code is ActionIssueCode.DUPLICATE_ACTION_TYPE
    assert application.registration_count == 1
    assert first.action_type.name == "refund"


def test_freeze_refuses_late_registration_without_changing_the_catalog() -> None:
    application = ActionApplication[Dependencies]()
    application.register(specification(), recipe())
    application.freeze()

    with pytest.raises(ActionApplicationError) as captured:
        application.register(
            specification(
                action_type=ActionType(namespace="example.billing", name="capture", version=1)
            ),
            recipe(),
        )

    assert captured.value.code is ActionIssueCode.REGISTRATION_FROZEN
    assert application.registration_count == 1
    assert application.is_frozen


def test_recipe_and_handle_are_frozen_plain_python_values() -> None:
    declared_recipe = recipe()
    registered = ActionApplication[Dependencies]().register(specification(), declared_recipe)

    with pytest.raises(FrozenInstanceError):
        declared_recipe.bind = _unreachable_bind
    with pytest.raises(FrozenInstanceError):
        registered.action_type = ActionType(namespace="example.billing", name="capture", version=1)


def test_public_plain_python_annotations_are_runtime_resolvable() -> None:
    component_hints = get_type_hints(ActionComponents)
    recipe_hints = get_type_hints(ActionRecipe)

    assert component_hints["store"] is not None
    assert recipe_hints["bind"] is not None


def test_application_has_no_public_dynamic_lookup() -> None:
    application = ActionApplication[Dependencies]()

    assert not hasattr(application, "get")
    assert not hasattr(application, "get_typed")
    assert not hasattr(application, "lookup")
