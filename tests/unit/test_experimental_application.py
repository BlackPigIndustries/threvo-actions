from __future__ import annotations

import asyncio
import gc
import weakref
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import get_type_hints
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from threvo_actions.canonical import (
    CommitmentProvider,
    KeyedCommitment,
    ProtectedPayload,
    ProtectionCodec,
)
from threvo_actions.experimental import (
    ActionApplication,
    ActionApplicationError,
    ActionComponents,
    ActionIssueCode,
    ActionRecipe,
    ActionSpec,
)
from threvo_actions.models import (
    ActionType,
    AuthoritativeTarget,
    GovernedExecutor,
    RequestingPrincipal,
)
from threvo_actions.registry import (
    AuthorizationPort,
    AuthorizationResult,
    GovernedExecutorPort,
    PreparationPort,
    PreparedAction,
    RetentionPort,
    StateResolverPort,
    VerifierPort,
)
from threvo_actions.runtime import OperationOutcome
from threvo_actions.stores import MemoryActionStore


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
    def __init__(self) -> None:
        self.closed = False
        self.store = MemoryActionStore()

    def close(self) -> None:
        self.closed = True


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class SequenceIdentifiers:
    def __init__(self) -> None:
        self.value = 0

    def new(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}:{self.value}"


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


def _components(
    dependencies: Dependencies,
) -> ActionComponents[Command, PrivateSnapshot, Preview, Result]:
    preparation = Mock(spec=PreparationPort)
    preparation.dependencies = dependencies
    preparation.prepare = AsyncMock(
        return_value=PreparedAction(
            private_snapshot=PrivateSnapshot(reference="snapshot:test"),
            display_preview=Preview(summary="Refund the payment"),
            semantic_effect_reference="effect:test",
        )
    )
    authorization = Mock(spec=AuthorizationPort)
    authorization.can_prepare = AsyncMock(return_value=AuthorizationResult(allowed=True))
    commitment = Mock(spec=CommitmentProvider)
    commitment.create = AsyncMock(
        return_value=KeyedCommitment(
            algorithm="hmac-sha256",
            key_handle="key:test",
            key_version="1",
            digest="digest:test",
        )
    )
    protection = Mock(spec=ProtectionCodec)
    protection.protect = AsyncMock(
        return_value=ProtectedPayload(
            codec="test",
            key_handle="key:test",
            key_version="1",
            ciphertext="opaque",
        )
    )
    return ActionComponents(
        preparation=preparation,
        authorization=authorization,
        authority_evaluator=Mock(),
        state_resolver=Mock(spec=StateResolverPort),
        executor=Mock(spec=GovernedExecutorPort),
        verifier=Mock(spec=VerifierPort),
        commitment_provider=commitment,
        protection_codec=protection,
        retention=Mock(spec=RetentionPort),
        store=dependencies.store,
        clock=FixedClock(),
        identifiers=SequenceIdentifiers(),
        runtime_revision="threvo-actions/0.1.3",
    )


def bound_recipe() -> ActionRecipe[Dependencies, Command, PrivateSnapshot, Preview, Result]:
    return ActionRecipe(bind=_components)


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
    "updates",
    (
        {"proposal_ttl": timedelta(0)},
        {"verification_delay": timedelta(seconds=-1)},
        {"max_verification_attempts": 0},
        {"verification_lease_duration": timedelta(0)},
        {"authority_audience": " unsafe"},
    ),
)
def test_specification_rejects_invalid_static_semantics(updates: dict[str, object]) -> None:
    with pytest.raises(ActionApplicationError) as captured:
        specification(**updates)

    assert captured.value.code is ActionIssueCode.INVALID_SPECIFICATION


def test_specification_rejects_nonconforming_boundary_models() -> None:
    with pytest.raises(ActionApplicationError) as mutable_error:
        specification(command_model=MutableCommand)

    with pytest.raises(ActionApplicationError) as float_error:
        specification(private_snapshot_model=FloatSnapshot)

    assert mutable_error.value.code is ActionIssueCode.INVALID_SPECIFICATION
    assert float_error.value.code is ActionIssueCode.INVALID_SPECIFICATION


def test_specification_failure_does_not_retain_rejected_input() -> None:
    rejected_input = " SENTINEL_TENANT_SECRET_123"

    with pytest.raises(ActionApplicationError) as captured:
        specification(authority_audience=rejected_input)

    error = captured.value
    assert error.code is ActionIssueCode.INVALID_SPECIFICATION
    assert str(error) == "action specification is invalid"
    assert rejected_input not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


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


def test_binding_requires_a_frozen_catalog() -> None:
    application = ActionApplication[Dependencies]()
    registered = application.register(specification(), bound_recipe())

    with (
        pytest.raises(ActionApplicationError) as captured,
        application.bind(registered, dependencies=Dependencies()),
    ):
        pass

    assert captured.value.code is ActionIssueCode.INCOMPLETE_BINDING


def test_bound_facade_delegates_only_while_its_scope_is_active() -> None:
    application = ActionApplication[Dependencies]()
    registered = application.register(specification(), bound_recipe())
    application.freeze()
    dependencies = Dependencies()

    with application.bind(registered, dependencies=dependencies) as bound:
        prepared = asyncio.run(
            bound.prepare(
                tenant_reference="tenant:test",
                command=Command(reference="payment:test"),
                requesting_principal=RequestingPrincipal(reference="user:test"),
            )
        )

    assert prepared.outcome is OperationOutcome.PREPARED
    assert not dependencies.closed
    with pytest.raises(ActionApplicationError) as captured:
        asyncio.run(
            bound.prepare(
                tenant_reference="tenant:test",
                command=Command(reference="payment:test"),
                requesting_principal=RequestingPrincipal(reference="user:test"),
            )
        )
    assert captured.value.code is ActionIssueCode.BINDING_INACTIVE


def test_bound_facade_has_no_public_definition_or_runtime_escape() -> None:
    application = ActionApplication[Dependencies]()
    registered = application.register(specification(), bound_recipe())
    application.freeze()

    with application.bind(registered, dependencies=Dependencies()) as bound:
        public_names = {name for name in dir(bound) if not name.startswith("_")}

    assert "definition" not in public_names
    assert "runtime" not in public_names
    assert "components" not in public_names


def test_repeated_bindings_keep_tenant_scoped_services_separate() -> None:
    application = ActionApplication[Dependencies]()
    registered = application.register(specification(), bound_recipe())
    application.freeze()
    first_dependencies = Dependencies()
    second_dependencies = Dependencies()

    with (
        application.bind(registered, dependencies=first_dependencies) as first,
        application.bind(registered, dependencies=second_dependencies) as second,
    ):
        first_result = asyncio.run(
            first.prepare(
                tenant_reference="tenant:first",
                command=Command(reference="payment:first"),
                requesting_principal=RequestingPrincipal(reference="user:first"),
            )
        )
        second_result = asyncio.run(
            second.prepare(
                tenant_reference="tenant:second",
                command=Command(reference="payment:second"),
                requesting_principal=RequestingPrincipal(reference="user:second"),
            )
        )

    first_record = asyncio.run(
        first_dependencies.store.get("tenant:first", first_result.proposal_reference)
    )
    leaked_record = asyncio.run(
        first_dependencies.store.get("tenant:second", second_result.proposal_reference)
    )
    second_record = asyncio.run(
        second_dependencies.store.get("tenant:second", second_result.proposal_reference)
    )

    assert first_dependencies.store is not second_dependencies.store
    assert first_record is not None
    assert leaked_record is None
    assert second_record is not None


def test_binding_rejects_a_handle_from_another_application() -> None:
    first = ActionApplication[Dependencies]()
    foreign = first.register(specification(), bound_recipe())
    first.freeze()
    second = ActionApplication[Dependencies]()
    second.freeze()

    with (
        pytest.raises(ActionApplicationError) as captured,
        second.bind(foreign, dependencies=Dependencies()),
    ):
        pass

    assert captured.value.code is ActionIssueCode.INCOMPLETE_BINDING


def test_recipe_failure_is_reported_without_host_exception_content() -> None:
    def fail(
        dependencies: Dependencies,
    ) -> ActionComponents[Command, PrivateSnapshot, Preview, Result]:
        del dependencies
        raise RuntimeError("tenant:secret database password")

    application = ActionApplication[Dependencies]()
    registered = application.register(specification(), ActionRecipe(bind=fail))
    application.freeze()

    with (
        pytest.raises(ActionApplicationError) as captured,
        application.bind(registered, dependencies=Dependencies()),
    ):
        pass

    assert captured.value.code is ActionIssueCode.INCOMPLETE_BINDING
    assert "secret" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_binding_rejects_none_for_a_required_component() -> None:
    def incomplete(
        dependencies: Dependencies,
    ) -> ActionComponents[Command, PrivateSnapshot, Preview, Result]:
        components = _components(dependencies)
        object.__setattr__(components, "authorization", None)
        return components

    application = ActionApplication[Dependencies]()
    registered = application.register(specification(), ActionRecipe(bind=incomplete))
    application.freeze()

    with (
        pytest.raises(ActionApplicationError) as captured,
        application.bind(registered, dependencies=Dependencies()),
    ):
        pass

    assert captured.value.code is ActionIssueCode.INCOMPLETE_BINDING


def test_scope_exit_releases_library_references_to_borrowed_dependencies() -> None:
    application = ActionApplication[Dependencies]()
    registered = application.register(specification(), bound_recipe())
    application.freeze()
    dependencies = Dependencies()
    dependency_reference = weakref.ref(dependencies)

    with application.bind(registered, dependencies=dependencies) as bound:
        assert dependency_reference() is dependencies

    del dependencies
    gc.collect()

    assert dependency_reference() is None
    with pytest.raises(ActionApplicationError, match="inactive"):
        asyncio.run(
            bound.prepare(
                tenant_reference="tenant:test",
                command=Command(reference="payment:test"),
                requesting_principal=RequestingPrincipal(reference="user:test"),
            )
        )
