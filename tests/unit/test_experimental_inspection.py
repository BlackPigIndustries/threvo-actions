from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError
from tests.unit.test_experimental_application import (
    Command,
    Dependencies,
    Preview,
    PrivateSnapshot,
    Result,
    specification,
)

from threvo_actions.experimental import (
    ActionApplication,
    ActionApplicationError,
    ActionComponents,
    ActionIssueCode,
    ActionRecipe,
)


class ExplosiveRecipe:
    def __init__(self) -> None:
        self.calls = 0
        self.dsn = "postgresql://user:password@private.example/actions"
        self.token = "sk_test_seeded_secret"  # noqa: S105  # synthetic leakage canary

    def __call__(
        self, dependencies: Dependencies
    ) -> ActionComponents[Command, PrivateSnapshot, Preview, Result]:
        del dependencies
        self.calls += 1
        raise AssertionError("static inspection must not bind")

    def __repr__(self) -> str:
        raise AssertionError("static inspection must not call repr")


def test_static_inspection_is_frozen_allowlisted_and_performs_no_io() -> None:
    factory = ExplosiveRecipe()
    application = ActionApplication[Dependencies]()
    registered = application.register(specification(), ActionRecipe(bind=factory))
    application.freeze()

    inspected = application.inspect(registered)

    assert factory.calls == 0
    assert inspected.action_type == registered.action_type
    assert [model.role for model in inspected.boundary_models] == [
        "command",
        "private_snapshot",
        "display_preview",
        "result",
    ]
    assert [model.name for model in inspected.boundary_models] == [
        "Command",
        "PrivateSnapshot",
        "Preview",
        "Result",
    ]
    assert inspected.settings.proposal_ttl == timedelta(minutes=10)
    assert inspected.source == "registered_recipe"
    assert inspected.catalog_frozen
    with pytest.raises(ValidationError, match="frozen"):
        inspected.catalog_frozen = False


def test_inspection_states_ownership_without_claiming_live_readiness() -> None:
    application = ActionApplication[Dependencies]()
    registered = application.register(specification(), ActionRecipe(bind=ExplosiveRecipe()))

    inspected = application.inspect(registered)

    assert inspected.ownership.dependency_scope == "host_owned"
    assert inspected.ownership.resources == "borrowed_per_binding"
    assert inspected.ownership.transaction_coherence == "host_enforced_not_verified"
    assert inspected.ownership.tenant_coherence == "host_enforced_not_verified"
    assert inspected.ownership.live_readiness == "not_evaluated"
    assert inspected.ownership.authorization == "action_specific_fail_closed"
    assert not inspected.catalog_frozen
    assert set(inspected.issue_codes) == {code.value for code in ActionIssueCode}


def test_inspection_rejects_a_foreign_handle_with_content_safe_error() -> None:
    first = ActionApplication[Dependencies]()
    foreign = first.register(specification(), ActionRecipe(bind=ExplosiveRecipe()))
    second = ActionApplication[Dependencies]()

    with pytest.raises(ActionApplicationError) as captured:
        second.inspect(foreign)

    assert captured.value.code is ActionIssueCode.INCOMPLETE_BINDING
    assert "refund" not in str(captured.value)


def test_inspection_uses_no_module_qualname_callable_or_dependency_content() -> None:
    forbidden = (
        "postgresql://user:password@private.example/actions",
        "sk_test_seeded_secret",
        "tenant:private",
        "principal:private",
        "key:private",
    )
    original_module = Command.__module__
    original_qualname = Command.__qualname__
    try:
        Command.__module__ = forbidden[0]
        Command.__qualname__ = forbidden[1]
        factory = ExplosiveRecipe()
        application = ActionApplication[Dependencies]()
        registered = application.register(specification(), ActionRecipe(bind=factory))

        serialized = application.inspect(registered).model_dump_json()
    finally:
        Command.__module__ = original_module
        Command.__qualname__ = original_qualname

    assert all(value not in serialized for value in forbidden)
    assert "live_readiness" in serialized
    assert "not_evaluated" in serialized
