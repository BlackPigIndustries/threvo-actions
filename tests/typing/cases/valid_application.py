from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar, assert_type

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

CommandT = TypeVar("CommandT", bound=BaseModel)
PrivateSnapshotT = TypeVar("PrivateSnapshotT", bound=BaseModel)
PreviewT = TypeVar("PreviewT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)
DepsT = TypeVar("DepsT")


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


class ActionSpec(BaseModel, Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    command_model: type[CommandT]
    private_snapshot_model: type[PrivateSnapshotT]
    preview_model: type[PreviewT]
    result_model: type[ResultT]


@dataclass(frozen=True)
class ActionPorts(Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    marker: str


@dataclass(frozen=True)
class ActionRecipe(Generic[DepsT, CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    bind: Callable[
        [DepsT],
        ActionPorts[CommandT, PrivateSnapshotT, PreviewT, ResultT],
    ]


@dataclass(frozen=True)
class RegisteredAction(Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    registration_id: int


@dataclass(frozen=True)
class BoundAction(Generic[CommandT, PrivateSnapshotT, PreviewT, ResultT]):
    registration_id: int


class ActionApplication(Generic[DepsT]):
    def register(
        self,
        specification: ActionSpec[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        recipe: ActionRecipe[DepsT, CommandT, PrivateSnapshotT, PreviewT, ResultT],
    ) -> RegisteredAction[CommandT, PrivateSnapshotT, PreviewT, ResultT]:
        del specification, recipe
        return RegisteredAction(registration_id=1)

    @contextmanager
    def bind(
        self,
        action: RegisteredAction[CommandT, PrivateSnapshotT, PreviewT, ResultT],
        dependencies: DepsT,
    ) -> Iterator[BoundAction[CommandT, PrivateSnapshotT, PreviewT, ResultT]]:
        del dependencies
        yield BoundAction(registration_id=action.registration_id)


specification = ActionSpec[Command, PrivateSnapshot, Preview, Result](
    command_model=Command,
    private_snapshot_model=PrivateSnapshot,
    preview_model=Preview,
    result_model=Result,
)
recipe = ActionRecipe[Dependencies, Command, PrivateSnapshot, Preview, Result](
    bind=lambda dependencies: ActionPorts(marker=dependencies.tenant_reference)
)
application = ActionApplication[Dependencies]()
registered = application.register(specification, recipe)
assert_type(
    registered,
    RegisteredAction[Command, PrivateSnapshot, Preview, Result],
)

with application.bind(registered, Dependencies(tenant_reference="tenant:test")) as bound:
    assert_type(bound, BoundAction[Command, PrivateSnapshot, Preview, Result])
