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

__all__ = [
    "ActionApplication",
    "ActionApplicationError",
    "ActionComponents",
    "ActionIssueCode",
    "ActionRecipe",
    "ActionSpec",
    "BoundAction",
    "DependencyScopeFactory",
    "RegisteredAction",
]
