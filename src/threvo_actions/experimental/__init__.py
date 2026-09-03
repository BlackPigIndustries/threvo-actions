"""Experimental gradual-reveal authoring API.

This namespace may change independently while it is evaluated. The expert
root API remains the stable runtime contract.
"""

from .application import (
    ActionApplication,
    ActionApplicationError,
    ActionComponents,
    ActionIssueCode,
    ActionRecipe,
    ActionSpec,
    BoundAction,
    DependencyScopeFactory,
    RegisteredAction,
)
from .inspection import (
    ActionInspection,
    ActionOwnershipInspection,
    ActionSettingsInspection,
    BoundaryModelInspection,
)

__all__ = [
    "ActionApplication",
    "ActionApplicationError",
    "ActionComponents",
    "ActionIssueCode",
    "ActionInspection",
    "ActionOwnershipInspection",
    "ActionRecipe",
    "ActionSpec",
    "ActionSettingsInspection",
    "BoundaryModelInspection",
    "BoundAction",
    "DependencyScopeFactory",
    "RegisteredAction",
]
